from extras.scripts import Script, StringVar, ObjectVar, BooleanVar
from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify

from users.models import Group, ObjectPermission
from extras.models import Tag, SavedFilter


class CreateManagerPackInteractive(Script):
    """
    Generic, re-usable NetBox Script.

    Prompts you for:
      - A friendly label (e.g. "Digital Signage")
      - An existing Tag (e.g. device-type-digital-signage)

    Then creates/updates:
      - Group: "Manager – <Label>"
      - Permissions:
          <Label> – Devices      (dcim.device)     view/change, constrained by device tag
          <Label> – Interfaces   (dcim.interface)  view/change, constrained by device tag via device__
          <Label> – Prefixes     (ipam.prefix)     view only, unconstrained
          <Label> – IP Addresses (ipam.ipaddress)  view/add/change, unconstrained (needed for creating new IPs)
      - Saved Filter:
          "My <Label> Devices" for dcim.device, filtered by tag (shared/public optional)
    """

    class Meta:
        name = "Create Manager Pack (Interactive, ID-safe)"
        description = "Creates/updates Group + Permissions + Saved Filter for a device-owner tag. Safe to re-run."
        scheduling_enabled = False

    label = StringVar(description="Friendly label (e.g. Digital Signage)")
    tag = ObjectVar(model=Tag, description="Select the Tag that identifies these devices (ownership tag)")
    shared_saved_filter = BooleanVar(
        description="Make the device saved filter shared/public (for dashboard Bookmarks widget)?",
        default=True,
    )

    def _ct_id(self, app_label: str, model: str) -> int:
        """
        Return ContentType ID (not the ContentType object).
        In your environment, .set() calls require IDs.
        """
        return ContentType.objects.get(app_label=app_label, model=model).id

    def _ensure_group(self, name: str) -> Group:
        group, created = Group.objects.get_or_create(name=name)
        if created:
            self.log_success(f"Created group: {group.name}")
        else:
            self.log_info(f"Group exists: {group.name}")
        return group

    def _ensure_permission(
        self,
        name: str,
        group: Group,
        object_type_ids: list[int],
        actions: list[str],
        constraints: dict,
    ) -> ObjectPermission:
        perm, created = ObjectPermission.objects.get_or_create(
            name=name,
            defaults={
                "enabled": True,
                "actions": actions,
                "constraints": constraints,
            },
        )

        # Idempotent update
        perm.enabled = True
        perm.actions = actions
        perm.constraints = constraints
        perm.save()

        # IMPORTANT: Use IDs for set() to avoid TypeError in your NetBox environment
        perm.groups.set([group.id])
        perm.object_types.set(object_type_ids)

        if created:
            self.log_success(f"Created permission: {name}")
        else:
            self.log_info(f"Updated permission: {name}")

        return perm

    def _ensure_saved_filter(
        self,
        name: str,
        slug: str,
        object_type_ids: list[int],
        parameters: dict,
        shared: bool,
    ) -> SavedFilter:
        sf, created = SavedFilter.objects.get_or_create(
            slug=slug,
            defaults={
                "name": name,
                "enabled": True,
                "shared": shared,
                "parameters": parameters,
            },
        )

        # Idempotent update
        sf.name = name
        sf.enabled = True
        sf.shared = shared
        sf.parameters = parameters
        sf.save()

        # IMPORTANT: Use IDs for set()
        sf.object_types.set(object_type_ids)

        if created:
            self.log_success(f"Created saved filter: {name} ({slug})")
        else:
            self.log_info(f"Updated saved filter: {name} ({slug})")

        return sf

    def run(self, data, commit):
        label: str = data["label"].strip()
        tag: Tag = data["tag"]
        shared: bool = data["shared_saved_filter"]

        if not label:
            self.log_failure("Label cannot be blank.")
            return

        # ContentType IDs (ID-safe)
        ct_device_id = self._ct_id("dcim", "device")
        ct_iface_id  = self._ct_id("dcim", "interface")
        ct_prefix_id = self._ct_id("ipam", "prefix")
        ct_ipaddr_id = self._ct_id("ipam", "ipaddress")

        # 1) Group
        group = self._ensure_group(f"Manager – {label}")

        # 2) Permissions (one permission per object type; ID-safe)
        self._ensure_permission(
            name=f"{label} – Devices",
            group=group,
            object_type_ids=[ct_device_id],
            actions=["view", "change"],
            constraints={"tags__slug": tag.slug},
        )

        self._ensure_permission(
            name=f"{label} – Interfaces",
            group=group,
            object_type_ids=[ct_iface_id],
            actions=["view", "change"],
            constraints={"device__tags__slug": tag.slug},
        )

        # Prefixes are typically shared; view-only, unconstrained.
        self._ensure_permission(
            name=f"{label} – Prefixes",
            group=group,
            object_type_ids=[ct_prefix_id],
            actions=["view"],
            constraints={},
        )

        # IMPORTANT:
        # IPAddress permission is left unconstrained so managers can create new IPs from prefixes.
        # NetBox cannot reliably constrain "new IPs that will later be assigned to tagged devices"
        # due to GenericForeignKey assignment behavior.
        self._ensure_permission(
            name=f"{label} – IP Addresses",
            group=group,
            object_type_ids=[ct_ipaddr_id],
            actions=["view", "add", "change"],
            constraints={},
        )

        # 3) Saved filter for Devices (used as the "bookmark" in the Dashboard Bookmarks widget)
        sf_name = f"My {label} Devices"
        sf_slug = f"my-{slugify(label)}-devices"

        self._ensure_saved_filter(
            name=sf_name,
            slug=sf_slug,
            object_type_ids=[ct_device_id],
            parameters={"tag": tag.slug},
            shared=shared,
        )

        self.log_success("Manager pack created/updated successfully.")
