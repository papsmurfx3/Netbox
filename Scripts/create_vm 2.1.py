"""
NetBox Script: Create a new VM (documentation-first) with:
- Next available IPv4 allocation from a selected Prefix
- Optional VLAN assignment per NIC
- Optional extra NICs (up to 2)
- Optional disks (up to 4) stored as VirtualDisk objects
- Optional Domain + VM DNS Name stored as VM custom fields
- IP DNS name stored on the IPAddress object

Tested patterns for NetBox 4.x (NetBox Cloud).
"""

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from dcim.models import DeviceRole, Platform
from dcim.choices import InterfaceModeChoices

from extras.models import Tag
from extras.scripts import (
    Script,
    StringVar,
    ObjectVar,
    MultiObjectVar,
    ChoiceVar,
    IntegerVar,
    TextVar,
)

from ipam.choices import IPAddressStatusChoices
from ipam.models import IPAddress, VRF, VLAN, Prefix

from tenancy.models import Tenant

from virtualization.choices import VirtualMachineStatusChoices
from virtualization.models import Cluster, VirtualMachine, VMInterface, VirtualDisk


class NewVM(Script):
    class Meta:
        name = "New VM v2.1"
        description = "Create a new VM with optional NICs/VLANs/disks and auto-allocate IPv4 from a prefix"

    #
    # Core VM fields
    #
    vm_name = StringVar(label="VM name")

    # This is used in two places:
    # - Stored on the IPAddress object as dns_name
    # - Optionally stored on the VM as a custom field (dns_name)
    dns_name = StringVar(label="DNS name", required=False)

    # Stored on the VM as a custom field (domain) if present
    domain_name = StringVar(label="Domain", required=False)

    vm_tags = MultiObjectVar(
        model=Tag,
        label="VM tags",
        required=False,
    )

    role = ObjectVar(
        model=DeviceRole,
        query_params={"vm_role": True},
        required=False,
        label="Role",
    )

    status = ChoiceVar(
        VirtualMachineStatusChoices,
        default=VirtualMachineStatusChoices.STATUS_ACTIVE,
        label="Status",
    )

    cluster = ObjectVar(model=Cluster, label="Cluster")

    tenant = ObjectVar(model=Tenant, required=False, label="Tenant")
    platform = ObjectVar(model=Platform, required=False, label="Platform / OS")

    vrf = ObjectVar(model=VRF, required=False, label="VRF (optional)")

    #
    # IPv4 allocation (NEW)
    #
    ipv4_prefix = ObjectVar(
        model=Prefix,
        label="IPv4 Prefix (allocate next available)",
        required=True,
    )

    #
    # NICs / VLANs
    #
    interface_name = StringVar(label="Primary interface name", default="eth0")

    interface_vlan = ObjectVar(
        model=VLAN,
        label="Primary NIC VLAN (untagged/access)",
        required=False,
    )

    extra_nics = IntegerVar(
        label="Number of additional NICs (0–2)",
        required=False,
    )

    nic2_name = StringVar(label="NIC 2 name", required=False)
    nic2_vlan = ObjectVar(model=VLAN, label="NIC 2 VLAN (untagged/access)", required=False)

    nic3_name = StringVar(label="NIC 3 name", required=False)
    nic3_vlan = ObjectVar(model=VLAN, label="NIC 3 VLAN (untagged/access)", required=False)

    #
    # Compute (optional)
    #
    vcpus = IntegerVar(label="VCPUs", required=False)
    memory = IntegerVar(label="Memory (MB)", required=False)

    #
    # Disks (optional) -> stored as VirtualDisk objects
    #
    disk1_name = StringVar(label="Disk 1 name", required=False, default="OS")
    disk1_size = IntegerVar(label="Disk 1 size (GB)", required=False)

    disk2_name = StringVar(label="Disk 2 name", required=False)
    disk2_size = IntegerVar(label="Disk 2 size (GB)", required=False)

    disk3_name = StringVar(label="Disk 3 name", required=False)
    disk3_size = IntegerVar(label="Disk 3 size (GB)", required=False)

    disk4_name = StringVar(label="Disk 4 name", required=False)
    disk4_size = IntegerVar(label="Disk 4 size (GB)", required=False)

    comments = TextVar(label="Comments", required=False)

    def run(self, data, commit):

        # Helper to log and still support dry-run behavior
        def log_plan(msg: str):
            if commit:
                self.log_info(msg)
            else:
                self.log_info(f"[DRY-RUN] {msg}")

        #
        # Basic validations for prefix/vrf/vlan relationship (to avoid confusing results)
        #
        prefix = data["ipv4_prefix"]
        selected_vrf = data.get("vrf")

        # Ensure VRF matches between selected Prefix and selected VRF
        if prefix.vrf != selected_vrf:
            raise RuntimeError(
                f"Selected Prefix {prefix.prefix} is in VRF '{prefix.vrf}', "
                f"but you selected VRF '{selected_vrf}'. These must match."
            )

        # If you select a VLAN for the primary NIC and the Prefix has a VLAN, enforce match
        # (Prefix.vlan can be null; in that case we don't enforce)
        if data.get("interface_vlan") and prefix.vlan and data["interface_vlan"] != prefix.vlan:
            raise RuntimeError(
                f"Selected Prefix {prefix.prefix} is associated to VLAN '{prefix.vlan}', "
                f"but you selected primary NIC VLAN '{data['interface_vlan']}'. "
                f"Choose the matching VLAN or a different prefix."
            )

        #
        # 1) Create VM
        #
        vm = VirtualMachine(
            name=data["vm_name"],
            role=data.get("role"),
            status=data["status"],
            cluster=data["cluster"],
            platform=data.get("platform"),
            vcpus=data.get("vcpus"),
            memory=data.get("memory"),
            comments=data.get("comments") or "",
            tenant=data.get("tenant"),
        )

        if commit:
            vm.full_clean()
            vm.save()
            vm.tags.set(data.get("vm_tags") or [])
        log_plan(f"Created VM record: {vm.name} (cluster={data['cluster']})")

        #
        # 2) Store VM custom fields (Domain + VM DNS Name) if they exist
        #
        # These require custom fields on VirtualMachine with Names:
        # - domain
        # - dns_name
        if commit:
            changed_cf = False
            if data.get("domain_name"):
                vm.custom_field_data["domain"] = data["domain_name"]
                changed_cf = True
            if data.get("dns_name"):
                vm.custom_field_data["dns_name"] = data["dns_name"]
                changed_cf = True
            if changed_cf:
                # This can raise ValidationError if custom field Names do not exist
                vm.full_clean()
                vm.save()
                log_plan("Updated VM custom fields (domain/dns_name).")

        #
        # 3) Helper to create an interface with optional untagged VLAN
        #
        def create_interface(name, vlan):
            if not name:
                return None

            iface = VMInterface(
                name=name,
                virtual_machine=vm,
            )

            if vlan:
                iface.mode = InterfaceModeChoices.MODE_ACCESS
                iface.untagged_vlan = vlan

            if commit:
                iface.full_clean()
                iface.save()

            vlan_info = f" (untagged VLAN: {vlan})" if vlan else ""
            log_plan(f"Created interface: {name}{vlan_info}")
            return iface

        #
        # Primary NIC
        #
        primary_vlan = data.get("interface_vlan")

        # If user did not pick a VLAN but the Prefix has a VLAN, auto-use it (nice quality-of-life)
        if not primary_vlan and prefix.vlan:
            primary_vlan = prefix.vlan
            log_plan(f"Primary NIC VLAN not selected; using Prefix VLAN: {primary_vlan}")

        primary_iface = create_interface(data["interface_name"], primary_vlan)

        #
        # 4) Allocate next available IPv4 from prefix and assign to primary interface
        #
        def allocate_next_ipv4_from_prefix(pfx: Prefix) -> str:
            # NetBox Prefix.get_available_ips() yields available host IPs in the prefix
            available = pfx.get_available_ips()
            try:
                next_ip = next(iter(available))
            except StopIteration:
                raise RuntimeError(f"No available IPv4 addresses remain in prefix {pfx.prefix}")

            # Use the prefix length on the IP (e.g. 10.0.0.5/23)
            return f"{next_ip}/{pfx.prefix.prefixlen}"

        allocated_ipv4 = allocate_next_ipv4_from_prefix(prefix)
        log_plan(f"Allocated next available IPv4 from {prefix.prefix}: {allocated_ipv4}")

        if commit:
            ip4 = IPAddress(
                address=allocated_ipv4,
                vrf=selected_vrf,
                status=IPAddressStatusChoices.STATUS_ACTIVE,
                dns_name=data.get("dns_name") or "",
                assigned_object=primary_iface,
                tenant=data.get("tenant"),
            )
            ip4.full_clean()
            ip4.save()

            vm.primary_ip4 = ip4
            vm.full_clean()
            vm.save()

            log_plan(f"Created/assigned IP: {ip4.address} -> {vm.name}:{primary_iface.name}")

        #
        # 5) Extra NICs (up to 2)
        #
        extra_nics = data.get("extra_nics") or 0

        if extra_nics >= 1:
            create_interface(data.get("nic2_name"), data.get("nic2_vlan"))
        if extra_nics >= 2:
            create_interface(data.get("nic3_name"), data.get("nic3_vlan"))
        if extra_nics > 2:
            log_plan("extra_nics > 2 provided; only 2 extra NICs are supported by this script.")

        #
        # 6) Virtual disks (up to 4)
        #
        def create_disk(name, size_gb):
            if not size_gb:
                return None

            disk_name = name or f"disk-{VirtualDisk.objects.filter(virtual_machine=vm).count() + 1}"
            disk = VirtualDisk(
                virtual_machine=vm,
                name=disk_name,
                # NetBox stores disk size in MB
                size=int(size_gb) * 1024,
            )

            if commit:
                disk.full_clean()
                disk.save()

            log_plan(f"Created disk: {disk_name} ({size_gb} GB)")
            return disk

        disks = []
        disks.append(create_disk(data.get("disk1_name"), data.get("disk1_size")))
        disks.append(create_disk(data.get("disk2_name"), data.get("disk2_size")))
        disks.append(create_disk(data.get("disk3_name"), data.get("disk3_size")))
        disks.append(create_disk(data.get("disk4_name"), data.get("disk4_size")))
        disks = [d for d in disks if d]

        #
        # 7) Build Summary (job output "report")
        #
        self.log_info("---- Build Summary ----")
        self.log_info(f"VM: {data['vm_name']}")
        self.log_info(f"Cluster: {data['cluster']}")
        if data.get("platform"):
            self.log_info(f"Platform/OS: {data['platform']}")
        if data.get("domain_name"):
            self.log_info(f"Domain: {data['domain_name']}")
        if data.get("dns_name"):
            self.log_info(f"DNS Name: {data['dns_name']}")

        self.log_info(f"IPv4 Prefix: {prefix.prefix}")
        self.log_info(f"Allocated IPv4: {allocated_ipv4}")

        self.log_info("Interfaces (with VLAN if set):")
        self.log_info(f"  - {data['interface_name']}" + (f" (VLAN: {primary_vlan})" if primary_vlan else ""))

        if extra_nics >= 1 and data.get("nic2_name"):
            self.log_info(f"  - {data.get('nic2_name')}" + (f" (VLAN: {data.get('nic2_vlan')})" if data.get("nic2_vlan") else ""))
        if extra_nics >= 2 and data.get("nic3_name"):
            self.log_info(f"  - {data.get('nic3_name')}" + (f" (VLAN: {data.get('nic3_vlan')})" if data.get("nic3_vlan") else ""))

        if disks:
            self.log_info("Disks:")
            for d in disks:
                # d.size is MB; convert back to GB for display
                gb = int(d.size / 1024) if commit else "?"
                self.log_info(f"  - {d.name}: {gb} GB")

        if commit:
            self.log_success(f"Created VM {vm.name} successfully.")
        else:
            self.log_success("Dry-run complete (no changes saved).")

        return "Done"
