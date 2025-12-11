"""
Create a new VM with optional extra NICs, VLANs, and disks.

Based on the community example:
https://github.com/netbox-community/customizations/blob/master/scripts/create_vm.py
"""

from dcim.models import DeviceRole, Platform
from dcim.choices import InterfaceModeChoices
from django.core.exceptions import ObjectDoesNotExist
from extras.models import Tag
from extras.scripts import (
    Script,
    StringVar,
    IPAddressWithMaskVar,
    ObjectVar,
    MultiObjectVar,
    ChoiceVar,
    IntegerVar,
    TextVar,
)
from ipam.choices import IPAddressStatusChoices
from ipam.models import IPAddress, VRF, VLAN
from tenancy.models import Tenant
from virtualization.choices import VirtualMachineStatusChoices
from virtualization.models import Cluster, VirtualMachine, VMInterface, VirtualDisk


class NewVM(Script):

    class Meta:
        name = "New VM v2"
        description = "Create a new VM with optional extra NICs, VLANs, and disks"

    #
    # Core VM fields
    #
    vm_name = StringVar(label="VM name")

    dns_name = StringVar(label="DNS name", required=False)

    # Optional: AD / DNS domain name (we'll store this as a custom field)
    domain_name = StringVar(label="Domain", required=False)

    vm_tags = MultiObjectVar(
        model=Tag,
        label="VM tags",
        required=False,
    )

    primary_ip4 = IPAddressWithMaskVar(label="IPv4 address")
    primary_ip6 = IPAddressWithMaskVar(label="IPv6 address", required=False)

    vrf = ObjectVar(model=VRF, required=False)

    role = ObjectVar(
        model=DeviceRole,
        query_params=dict(vm_role=True),
        required=False,
    )

    status = ChoiceVar(
        VirtualMachineStatusChoices,
        default=VirtualMachineStatusChoices.STATUS_ACTIVE,
    )

    cluster = ObjectVar(model=Cluster)

    tenant = ObjectVar(model=Tenant, required=False)
    platform = ObjectVar(model=Platform, required=False)

    #
    # Primary NIC
    #
    interface_name = StringVar(
        label="Primary interface name",
        default="eth0",
    )

    interface_vlan = ObjectVar(
        model=VLAN,
        label="Primary NIC VLAN (untagged/access)",
        required=False,
    )

    #
    # Extra NICs (fixed max = 2 for now; increase later if you want)
    #
    extra_nics = IntegerVar(
        label="Number of additional NICs (0–2)",
        required=False,
    )

    nic2_name = StringVar(label="NIC 2 name", required=False)
    nic2_vlan = ObjectVar(
        model=VLAN,
        label="NIC 2 VLAN (untagged/access)",
        required=False,
    )

    nic3_name = StringVar(label="NIC 3 name", required=False)
    nic3_vlan = ObjectVar(
        model=VLAN,
        label="NIC 3 VLAN (untagged/access)",
        required=False,
    )

    #
    # Compute
    #
    vcpus = IntegerVar(label="VCPUs", required=False)
    memory = IntegerVar(label="Memory (MB)", required=False)

    #
    # Disks – these become VirtualDisk objects attached to the VM
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

        #
        # 1) Create the VM
        #
        vm = VirtualMachine(
            name=data["vm_name"],
            role=data["role"],
            status=data["status"],
            cluster=data["cluster"],
            platform=data["platform"],
            vcpus=data["vcpus"],
            memory=data["memory"],
            comments=data["comments"],
            tenant=data.get("tenant"),
        )
        vm.full_clean()
        vm.save()
        vm.tags.set(data["vm_tags"])

        # Store domain and DNS name as VM custom fields, if you have them
        # NOTE: adjust the keys "domain" and "dns_name" to match your actual
        # custom field *names* (not labels) if they differ.
        changed_cf = False
        if data.get("domain_name"):
            vm.custom_field_data["domain"] = data["domain_name"]
            changed_cf = True
        if data.get("dns_name"):
            vm.custom_field_data["dns_name"] = data["dns_name"]
            changed_cf = True
        if changed_cf:
            vm.save()

        self.log_info(f"Created VM {vm.name} in cluster {vm.cluster}")

        #
        # 2) Helper to create NICs
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

            iface.full_clean()
            iface.save()

            vlan_info = f", VLAN {vlan}" if vlan else ""
            self.log_info(f"Created interface {iface.name}{vlan_info}")
            return iface

        # Primary NIC (used for primary IP)
        primary_iface = create_interface(
            data["interface_name"],
            data.get("interface_vlan"),
        )

        #
        # 3) Extra NICs (up to 2)
        #
        extra_nics = data.get("extra_nics") or 0

        if extra_nics >= 1:
            create_interface(
                data.get("nic2_name"),
                data.get("nic2_vlan"),
            )

        if extra_nics >= 2:
            create_interface(
                data.get("nic3_name"),
                data.get("nic3_vlan"),
            )

        #
        # 4) Helper to create / assign IP addresses
        #
        def add_addr(addr, family):
            if not addr:
                return

            if addr.version != family:
                raise RuntimeError(f"Wrong address family for {addr}")

            try:
                a = IPAddress.objects.get(
                    address=addr,
                    vrf=data.get("vrf"),
                )
                a.snapshot()
                result = "Assigned"
            except ObjectDoesNotExist:
                a = IPAddress(
                    address=addr,
                    vrf=data.get("vrf"),
                )
                result = "Created"

            a.status = IPAddressStatusChoices.STATUS_ACTIVE
            a.dns_name = data["dns_name"]

            if a.assigned_object:
                raise RuntimeError(f"Address {addr} is already assigned")

            # Always bind primary IPs to the primary interface
            a.assigned_object = primary_iface
            a.tenant = data.get("tenant")
            a.full_clean()
            a.save()

            self.log_info(
                f"{result} IP address {a.address} "
                f"{a.vrf or ''} and assigned to {primary_iface.name}"
            )
            setattr(vm, f"primary_ip{family}", a)
            vm.snapshot()

        add_addr(data["primary_ip4"], 4)
        add_addr(data["primary_ip6"], 6)

        vm.full_clean()
        vm.save()

        #
        # 5) Create Virtual Disks
        #
        def create_disk(name, size_gb):
            if not size_gb:
                return None

            disk_name = name or f"disk-{VirtualDisk.objects.filter(virtual_machine=vm).count() + 1}"
            disk = VirtualDisk(
                virtual_machine=vm,
                name=disk_name,
                size=size_gb * 1024,  # NetBox stores size in MB
            )
            disk.full_clean()
            disk.save()
            self.log_info(f"Created disk {disk.name} ({size_gb} GB)")
            return disk

        disks = []
        disks.append(create_disk(data.get("disk1_name"), data.get("disk1_size")))
        disks.append(create_disk(data.get("disk2_name"), data.get("disk2_size")))
        disks.append(create_disk(data.get("disk3_name"), data.get("disk3_size")))
        disks.append(create_disk(data.get("disk4_name"), data.get("disk4_size")))
        disks = [d for d in disks if d]

        #
        # 6) Build Summary ("report") in job output
        #
        self.log_info("---- Build Summary ----")
        self.log_info(f"VM: {vm.name}")
        self.log_info(f"Cluster: {vm.cluster}")
        if vm.platform:
            self.log_info(f"Platform / OS: {vm.platform}")
        if data.get("domain_name"):
            self.log_info(f"Domain: {data['domain_name']}")
        if vm.primary_ip4:
            self.log_info(f"Primary IPv4: {vm.primary_ip4} (DNS: {vm.primary_ip4.dns_name})")
        if vm.primary_ip6:
            self.log_info(f"Primary IPv6: {vm.primary_ip6} (DNS: {vm.primary_ip6.dns_name})")

        # Interfaces
        self.log_info("Interfaces:")
        for iface in vm.interfaces.all().order_by("name"):
            vlan = iface.untagged_vlan
            vlan_str = f" VLAN {vlan}" if vlan else ""
            self.log_info(f"  - {iface.name}{vlan_str}")

        # Disks
        if disks:
            self.log_info("Disks:")
            for d in disks:
                self.log_info(f"  - {d.name}: {int(d.size / 1024)} GB")

        self.log_success(
            f"Created VM {vm.name} "
            f"(/virtualization/virtual-machines/{vm.id}/)"
        )
