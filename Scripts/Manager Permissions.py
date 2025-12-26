from extras.scripts import Script, TextVar, BooleanVar
from django.contrib.contenttypes.models import ContentType
from django.utils.text import slugify

from users.models import Group, ObjectPermission
from extras.models import Tag, SavedFilter


class ManagerPackGenerator(Script):
    class Meta:
        name = "Manager Pack Generator (Generic)"
        description = (
            "Create/update a standardized manager pack (Tag, Group, Permissions, Saved Filter) "
            "for one or more device-type tags. Safe to re-run."
        )
        scheduling_enabled = False

    # Bulk input: one per line, format: Label,tag-slug
    # Example:
    # Digital Signage,device-type-digital-signage
    # Cameras,device-type-cameras
    definitions = TextVar(
        description=(
            "Enter one or more lines in the format: Label,tag-slug\n"
            "Example:\n"
            "Digital Signage,device-type-digital-signage\n"
            "Cameras,device-type-cameras"
        )
    )

    create_missing_tags = BooleanVar(
        description="Create the Tag if it does not already exist?",
        default=True
    )

    shared_saved_filters = BooleanVar(
        description="Make Saved Filters shared/public (so they can appear in Bookmarks widgets)?",
        default=True
    )

    def _ct(self, app_label: str, model: str) -> ContentType:
        return ContentType.objects.get(app_label=app_label, model=model)

    def _ensure_tag(self, slug: str, create_missing: bool) -> Tag | None:
        tag = Tag.objects.filter(slug=slug).first()
        if tag:
            self.log_info(f"Tag exists: {tag.slug}")
            return tag
        if not create_missing:
            self.log_warning(f"Tag missing and create_missing_tags is False: {slug}")
            return None
        tag = Tag.objects.create(name=slug, slug=slug)
        self.log_success(f"Created tag: {tag.slug}")
        return tag

    def _ensure_group(self, name: str) -> Group:
        group, created = Group.objects.get_or_create(name=name)
        if created:
            self.log_success(f"Created group: {group.name}")
        else:
            self.log_info(f"Group exists: {group.name}")
        return group

    def _ensure_permission(self, name: str, group: Group, object_types, actions, constraints: dict):
        perm, created = ObjectPermission.objects.get_or_create(
            name=name,
            defaults={
                "enabled": True,
                "actions": actions,
                "constraints": constraints,
            }
        )

        # Idempotent update
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

    def _ensure_saved_filter(self, label: str, tag_slug: str, ct_device: ContentType, shared: bool):
        sf_name = f"My {label} Devices"
        sf_slug = f"my-{slugify(label)}-devices"

        sf, created = SavedFilter.objects.get_or_create(
            slug=sf_slug,
            defaults={
                "name": sf_name,
                "enabled": True,
                "shared": shared,
                "parameters": {"tag": tag_slug},
            }
        )

        # Idempotent update
        sf.name = sf_name
        sf.enabled = True
        sf.shared = shared
        sf.parameters = {"tag": tag_slug}
        sf.save()
        sf.object_types.set([ct_device])

        if created:
            self.log_success(f"Created saved filter: {sf_name} ({sf_slug})")
        else:
            self.log_info(f"Updated saved filter: {sf_name} ({sf_slug})")

    def run(self, data, commit):
        create_tags = data["create_missing_tags"]
        shared_filters = data["shared_saved_filters"]

        ct_device = self._ct("dcim", "device")
        ct_iface  = self._ct("dcim", "interface")
        ct_prefix = self._ct("ipam", "prefix")
        ct_ipaddr = self._ct("ipam", "ipaddress")

        lines = [ln.strip() for ln in data["definitions"].splitlines() if ln.strip()]
        if not lines:
            self.log_failure("No definitions provided.")
            return

        for ln in lines:
            # Expect: Label,tag-slug
            if "," not in ln:
                self.log_warning(f"Skipping (missing comma): {ln}")
                continue

            label, tag_slug = [p.strip() for p in ln.split(",", 1)]
            if not label or not tag_slug:
                self.log_warning(f"Skipping (empty label or tag): {ln}")
                continue

            self.log_info(f"--- Processing: {label} / {tag_slug} ---")

            # Tag
            tag = self._ensure_tag(tag_slug, create_tags)
            if tag is None:
                self.log_warning(f"Skipping pack (tag missing): {tag_slug}")
                continue

            # Group
            group = self._ensure_group(f"Manager – {label}")

            # Permissions
            # Devices (scoped by device tag)
            self._ensure_permission(
                name=f"{label} – Devices",
                group=group,
                object_types=[ct_device],
                actions=["view", "change"],
                constraints={"tags__slug": tag_slug},
            )

            # Interfaces (scoped by device tag)
            self._ensure_permission(
                name=f"{label} – Interfaces",
                group=group,
                object_types=[ct_iface],
                actions=["view", "change"],
                constraints={"device__tags__slug": tag_slug},
            )

            # Prefixes (view-only, global)
            self._ensure_permission(
                name=f"{label} – Prefixes",
                group=group,
                object_types=[ct_prefix],
                actions=["view"],
                constraints={},
            )

            # IP Addresses (unconstrained so managers can create new IPs from prefixes)
            self._ensure_permission(
                name=f"{label} – IP Addresses",
                group=group,
                object_types=[ct_ipaddr],
                actions=["view", "add", "change"],
                constraints={},
            )

            # Saved filter (used for dashboard Bookmarks widget)
            self._ensure_saved_filter(label, tag_slug, ct_device, shared_filters)

        self.log_success("All manager packs processed.")
