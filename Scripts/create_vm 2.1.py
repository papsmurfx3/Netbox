"""
NetBox Script: Create a new VM (documentation-first) with:
- IPv4 allocation: next available from a selected Prefix OR a selected IP Range
- Optional VLAN assignment per NIC (access/untagged)
- Optional extra NICs (up to 2)
- Optional disks (up to 4) stored as VirtualDisk objects
- Optional Domain + VM DNS Name stored as VM custom fields
- IP DNS name stored on the IPAddress object

Designed for NetBox 4.x / NetBox Cloud.
"""

from django.core.exceptions import ObjectDoesNotExist, ValidationError

from netaddr import IPAddress as NAddrIPAddress

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
from ipam.models import IPAddress, VRF, VLAN, Prefix, IPRange

from tenancy.models import Tenant

from virtualization.choices import VirtualMachineStatusChoices
from virtualization.models import Cluster, VirtualMachine, VMInterface, VirtualDisk


class NewVM(Script):
    class Meta:
        name = "New VM 2.1"
        description = "Create a new VM with optional NICs/VLANs/disks and auto-allocate IPv4 from a Prefix or IP Range"

    #
    # Core VM fields
    #
    vm_name = StringVar(label="VM name")

    # Used in two places:
    # - Stored on the IPAddress object as dns_name
    # - Optionally stored on the VM as a custom field (dns_name)
    dns_name = StringVar(label="DNS name", required=False)

    # Optionally stored on the VM as a custom field (domain)
    domain_name = StringVar(label="Domain", required=False)

    vm_tags = MultiObjectVar(model=Tag, label="VM tags", required=False)

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
    # IPv4 allocation (Prefix OR Range)
    #
    allocation_source = ChoiceVar(
        choices=(
            ("prefix", "Prefix (next available)"),
            ("range", "IP Range (next available)"),
        ),
        default="prefix",
        label="IPv4 allocation source",
    )

    ipv4_prefix = ObjectVar(
        model=Prefix,
        label="IPv4 Prefix (allocate next available)",
        required=False,
    )

    ipv4_range = ObjectVar(
        model=IPRange,
        label="IPv4 Range (allocate next available)",
        required=False,
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

        def log_plan(msg: str):
            # Keep job output readable while still supporting dry-run
            if commit:
                self.log_info(msg)
            else:
                self.log_info(f"[DRY-RUN] {msg}")

        selected_vrf = data.get("vrf")

        #
        # Choose allocation object based on allocation_source
        #
        allocation_source = data["allocation_source"]
        pfx = data.get("ipv4_prefix")
        rng = data.get("ipv4_range")

        if allocation_source == "prefix" and not pfx:
            raise RuntimeError("IPv4 allocation source is Prefix, but no Prefix was selected.")
        if allocation_source == "range" and not rng:
            raise RuntimeError("IPv4 allocation source is IP Range, but no IP Range was selected.")

        # VRF must match (or both None)
        if allocation_source == "prefix":
            if pfx.vrf != selected_vrf:
                raise RuntimeError(
                    f"Selected Prefix {pfx.prefix} is in VRF '{pfx.vrf}', but you selected VRF '{selected_vrf}'. "
                    f"These must match."
                )
        else:
            # range
            if rng.vrf != selected_vrf:
                raise RuntimeError(
                    f"Selected IP Range {rng.start_address} - {rng.end_address} is in VRF '{rng.vrf}', "
                    f"but you selected VRF '{selected_vrf}'. These must match."
                )

        #
        # VLAN sanity checks (keeps your “must be within VLAN/subnet” intent consistent)
        #
        primary_vlan = data.get("interface_vlan")

        # If user didn’t pick VLAN, but the allocation object has VLAN, auto-use it.
        # (Only if those fields exist on your objects.)
        if not primary_vlan:
            if allocation_source == "prefix" and getattr(pfx, "vlan", None):
                primary_vlan = pfx.vlan
                log_plan(f"Primary NIC VLAN not selected; using Prefix VLAN: {primary_vlan}")
            if allocation_source == "range" and getattr(rng, "vlan", None):
                primary_vlan = rng.vlan
                log_plan(f"Primary NIC VLAN not selected; using Range VLAN: {primary_vlan}")

        # If both are set, enforce match when possible
        if primary_vlan:
            if allocation_source == "prefix" and getattr(pfx, "vlan", None) and pfx.vlan != primary_vlan:
                raise RuntimeError(
                    f"Selected Prefix {pfx.prefix} is associated with VLAN '{pfx.vlan}', "
                    f"but you selected primary NIC VLAN '{primary_vlan}'. Choose a matching VLAN/prefix."
                )
            if allocation_source == "range" and getattr(rng, "vlan", None) and rng.vlan != primary_vlan:
                raise RuntimeError(
                    f"Selected Range {rng.start_address}-{rng.end_address} is associated with VLAN '{rng.vlan}', "
                    f"but you selected primary NIC VLAN '{primary_vlan}'. Choose a matching VLAN/range."
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
        # 2) Store VM custom fields (domain/dns_name) if those CFs exist
        #
        if commit:
            try:
                changed_cf = False
                if data.get("domain_name"):
                    vm.custom_field_data["domain"] = data["domain_name"]
                    changed_cf = True
                if data.get("dns_name"):
                    vm.custom_field_data["dns_name"] = data["dns_name"]
                    changed_cf = True
                if changed_cf:
                    vm.full_clean()
                    vm.save()
                    log_plan("Updated VM custom fields (domain/dns_name).")
            except ValidationError:
                # If custom fields aren’t created, don’t fail the whole build
                self.log_warning(
                    "VM custom fields 'domain' and/or 'dns_name' are not defined on VirtualMachine. "
                    "Skipping writing those custom fields."
                )

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
        primary_iface = create_interface(data["interface_name"], primary_vlan)

        #
        # 4) IPv4 allocation
        #
        def allocate_next_ipv4_from_prefix(prefix: Prefix) -> str:
            available = prefix.get_available_ips()
            try:
                next_ip = next(iter(available))
            except StopIteration:
                raise RuntimeError(f"No available IPv4 addresses remain in prefix {prefix.prefix}")
            return f"{next_ip}/{prefix.prefix.prefixlen}"

        def _extract_ip_and_mask(value):
            """
            NetBox range endpoints are typically stored with a mask (e.g. 10.1.2.10/24).
            We normalize to (ip_as_string, prefixlen_int).
            """
            # value might be an IPNetwork-like object with .ip and .prefixlen
            if hasattr(value, "ip") and hasattr(value, "prefixlen"):
                return str(value.ip), int(value.prefixlen)

            # fallback: string like "10.1.2.10/24" or "10.1.2.10"
            s = str(value)
            if "/" in s:
                ip, mask = s.split("/", 1)
                return ip, int(mask)
            return s, 32  # last-resort fallback

        def allocate_next_ipv4_from_range(iprange: IPRange) -> str:
            start_ip_s, mask = _extract_ip_and_mask(iprange.start_address)
            end_ip_s, _mask2 = _extract_ip_and_mask(iprange.end_address)

            start = NAddrIPAddress(start_ip_s)
            end = NAddrIPAddress(end_ip_s)

            ip = start
            while ip <= end:
                candidate = f"{ip}/{mask}"
                # exists() is fast enough for typical ranges; if you have huge ranges, we can optimize
                if not IPAddress.objects.filter(address=candidate, vrf=selected_vrf).exists():
                    return candidate
                ip = NAddrIPAddress(int(ip) + 1)

            raise RuntimeError(
                f"No available IPv4 addresses remain in range {iprange.start_address} - {iprange.end_address}"
            )

        if allocation_source == "prefix":
            allocated_ipv4 = allocate_next_ipv4_from_prefix(pfx)
            log_plan(f"Allocated next available IPv4 from Prefix {pfx.prefix}: {allocated_ipv4}")
        else:
            allocated_ipv4 = allocate_next_ipv4_from_range(rng)
            log_plan(f"Allocated next available IPv4 from Range {rng.start_address}-{rng.end_address}: {allocated_ipv4}")

        #
        # 5) Create/assign IPAddress and set VM primary IPv4
        #
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
        # 6) Extra NICs (up to 2)
        #
        extra_nics = data.get("extra_nics") or 0

        if extra_nics >= 1:
            create_interface(data.get("nic2_name"), data.get("nic2_vlan"))
        if extra_nics >= 2:
            create_interface(data.get("nic3_name"), data.get("nic3_vlan"))
        if extra_nics > 2:
            log_plan("extra_nics > 2 provided; only 2 extra NICs are supported by this script.")

        #
        # 7) Virtual disks (up to 4)
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
        # 8) Build Summary (job output "report")
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

        if allocation_source == "prefix":
            self.log_info(f"IPv4 allocation: Prefix {pfx.prefix}")
        else:
            self.log_info(f"IPv4 allocation: Range {rng.start_address} - {rng.end_address}")

        self.log_info(f"Allocated IPv4: {allocated_ipv4}")

        self.log_info("Interfaces (with VLAN if set):")
        self.log_info(f"  - {data['interface_name']}" + (f" (VLAN: {primary_vlan})" if primary_vlan else ""))

        if extra_nics >= 1 and data.get("nic2_name"):
            self.log_info(
                f"  - {data.get('nic2_name')}"
                + (f" (VLAN: {data.get('nic2_vlan')})" if data.get("nic2_vlan") else "")
            )
        if extra_nics >= 2 and data.get("nic3_name"):
            self.log_info(
                f"  - {data.get('nic3_name')}"
                + (f" (VLAN: {data.get('nic3_vlan')})" if data.get("nic3_vlan") else "")
            )

        if disks:
            self.log_info("Disks:")
            for d in disks:
                gb = int(d.size / 1024) if commit else "?"
                self.log_info(f"  - {d.name}: {gb} GB")

        if commit:
            self.log_success(f"Created VM {vm.name} successfully.")
        else:
            self.log_success("Dry-run complete (no changes saved).")

        return "Done"
