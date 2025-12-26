from extras.scripts import Script, StringVar, ObjectVar, BooleanVar
from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify

from users.models import Group, ObjectPermission
from extras.models import Tag, SavedFilter


class CreateManagerPackInteractive(Script):
    class Meta:
        name = "Create Manager Pack (Interactive)"
        description = "Interactive: pick a Tag and enter a Label; script builds/updates group + permissions + saved filter."
        scheduling_enabled = False

    label = StringVar(description="Friendly label (e.g. Digital Signage)")
    tag = ObjectVar(model=Tag, description="Select the ownership tag for this device type")
    shared_saved_filter = BooleanVar(description="Make saved filter shared/public?", default=True)

    def _ct(self, app_label: str, model: str) -> ContentType:
        return ContentType.objects.get(app_label=app_label, model=model)

    def _ensure_permission(self, name: str, group: Group, object_types, actions, constraints: dict):
        perm, created = ObjectPermission.objects.get_or_create(
            name=name,
            defaults={"enabled": True, "actions": actions, "constraints": constraints},
        )
        perm.enabled = True
        perm.actions = actions
        perm.constraints = constraints
        perm.save()
        perm.groups.set([group])
        perm.object_types.set(object_types)

        if created:
            self.log_success(f"Created permission: {name}")
        else:
            self.log_info(f"Updated permission: {name}")

    def run(self, data, commit):
        label = data["label"].strip()
        tag = data["tag"]
        shared = data["shared_saved_filter"]

        group_name = f"Manager – {label}"
        group, _ = Group.objects.get_or_create(name=group_name)
        self.log_info(f"Using group: {group.name}")

        ct_device = self._ct("dcim", "device")
        ct_iface  = self._ct("dcim", "interface")
        ct_prefix = self._ct("ipam", "prefix")
        ct_ipaddr = self._ct("ipam", "ipaddress")

        # Devices
        self._ensure_permission(
            name=f"{label} – Devices",
            group=group,
            object_types=[ct_device],
            actions=["view", "change"],
            constraints={"tags__slug": tag.slug},
        )

        # Interfaces
        self._ensure_permission(
            name=f"{label} – Interfaces",
            group=group,
            object_types=[ct_iface],
            actions=["view", "change"],
            constraints={"device__tags__slug": tag.slug},
        )

        # Prefixes (view only)
        self._ensure_permission(
            name=f"{label} – Prefixes",
            group=group,
            object_types=[ct_prefix],
            actions=["view"],
            constraints={},
        )

        # IP Addresses (unconstrained so managers can create from prefix)
        self._ensure_permission(
            name=f"{label} – IP Addresses",
            group=group,
            object_types=[ct_ipaddr],
            actions=["view", "add", "change"],
            constraints={},
        )

        # Saved filter
        sf_name = f"My {label} Devices"
        sf_slug = f"my-{slugify(label)}-devices"
        sf, created = SavedFilter.objects.get_or_create(
            slug=sf_slug,
            defaults={
                "name": sf_name,
                "enabled": True,
                "shared": shared,
                "parameters": {"tag": tag.slug},
            },
        )
        sf.name = sf_name
        sf.enabled = True
        sf.shared = shared
        sf.parameters = {"tag": tag.slug}
        sf.save()
        sf.object_types.set([ct_device])

        self.log_success(f"{'Created' if created else 'Updated'} saved filter: {sf_name}")
        self.log_success("Done.")
