import re
from django.db import transaction

from extras.scripts import Script, ObjectVar, StringVar, BooleanVar
from dcim.models import Location, Device, FrontPort, RearPort

ROLE_NAME = "Copper Termination"
PATCH_PANEL_PHRASE = "Patch Panel"
TENANT_NAME = "CBSD Technology Department"


def _natural_sort_key(text):
    """
    Natural sort key for names like 1,2,10.
    """
    if text is None:
        return ([], "")
    parts = re.split(r"(\d+)", str(text))
    key = []
    for p in parts:
        if p.isdigit():
            key.append(int(p))
        else:
            key.append(p.lower())
    return (key, str(text).lower())


def _validated_save(obj):
    """
    Use validated_save() if available, otherwise full_clean()+save().
    """
    if hasattr(obj, "validated_save"):
        obj.validated_save()
    else:
        obj.full_clean()
        obj.save()


def _extract_patch_panel_id(device_name):
    """
    Case-insensitive find 'Patch Panel' and return the first token after it.
    Example:
      'IDF-A Patch Panel PP-01 Left' -> 'PP-01'
      'Patch Panel 3' -> '3'
    Returns None if not found.
    """
    if not device_name:
        return None
    m = re.search(r"\bPatch\s+Panel\b\s*(.+)$", device_name, flags=re.IGNORECASE)
    if not m:
        return None
    tail = (m.group(1) or "").strip()
    if not tail:
        return None
    # take first whitespace-separated token (keeps internal hyphens)
    first_token = tail.split()[0]
    return first_token or None


class RenamePatchPanelPortsCBSD(Script):
    class Meta:
        name = "Rename patch panel ports (Copper Termination, CBSD tenant, IDF)"
        description = (
            "Renames FrontPorts and RearPorts for devices in a chosen Location that "
            "have Role 'Copper Termination' and Tenant 'CBSD Technology Department'.\n"
            "Front: A-B-01 (label matches name)\n"
            "Rear:  A-B-01-R (rear label cleared)\n"
        )
        commit_default = False

    location = ObjectVar(
        model=Location,
        description="IDF/Location to scope changes to (only devices in this Location will be touched).",
    )

    idf_label = StringVar(
        description='The "A" in A-B-01 (e.g., "A", "IDF1", etc.).',
    )

    clear_rear_label = BooleanVar(
        default=True,
        description="If checked, rear port labels will be cleared (rear ports do not need a label).",
    )

    def run(self, data, commit):
        location = data["location"]
        idf_label = str(data["idf_label"]).strip()
        clear_rear_label = bool(data.get("clear_rear_label"))

        if not idf_label:
            self.log_failure("IDF label cannot be blank.")
            return

        devices_qs = (
            Device.objects.filter(
                location=location,
                role__name=ROLE_NAME,
                tenant__name=TENANT_NAME
            )
            .prefetch_related("frontports", "rearports", "role", "tenant")
            .order_by("name")
        )

        devices = list(devices_qs)
        if not devices:
            self.log_warning(
                f"No devices with Role '{ROLE_NAME}' and Tenant '{TENANT_NAME}' found in Location '{location}'."
            )
            return

        self.log_info(f"Location: {location} | Role: {ROLE_NAME} | Tenant: {TENANT_NAME}")
        self.log_info(f"IDF label (A): {idf_label}")
        self.log_info(f"Devices found: {len(devices)}")

        touched = 0
        skipped_no_phrase = 0
        skipped_no_id = 0
        skipped_no_ports = 0

        with transaction.atomic():
            for device in devices:
                name = device.name or ""

                # Must contain 'Patch Panel'
                if re.search(r"\bPatch\s+Panel\b", name, flags=re.IGNORECASE) is None:
                    skipped_no_phrase += 1
                    self.log_warning(
                        f"Skipping '{name}': does not contain '{PATCH_PANEL_PHRASE}'.",
                        obj=device,
                    )
                    continue

                panel_id = _extract_patch_panel_id(name)
                if not panel_id:
                    skipped_no_id += 1
                    self.log_warning(
                        f"Skipping '{name}': couldn't extract identifier after '{PATCH_PANEL_PHRASE}'.",
                        obj=device,
                    )
                    continue

                # Ensure device has at least one port to process
                if not (device.frontports.exists() or device.rearports.exists()):
                    skipped_no_ports += 1
                    self.log_warning(f"Skipping '{name}': no front or rear ports found.", obj=device)
                    continue

                touched += 1
                self.log_info(f"Processing '{name}' -> panel id '{panel_id}'", obj=device)

                # FRONT PORTS
                front_ports = list(FrontPort.objects.filter(device=device))
                front_ports.sort(key=lambda p: _natural_sort_key(p.name))

                for idx, fp in enumerate(front_ports, start=1):
                    new_name = f"{idf_label}-{panel_id}-{idx:02d}"
                    needs_change = (fp.name != new_name) or (fp.label != new_name)

                    fp.name = new_name
                    fp.label = new_name  # front label = name

                    if needs_change:
                        _validated_save(fp)
                        self.log_success(f"FrontPort -> {new_name}", obj=fp)
                    else:
                        self.log_info(f"FrontPort already correct -> {new_name}", obj=fp)

                # REAR PORTS
                rear_ports = list(RearPort.objects.filter(device=device))
                rear_ports.sort(key=lambda p: _natural_sort_key(p.name))

                for idx, rp in enumerate(rear_ports, start=1):
                    new_name = f"{idf_label}-{panel_id}-{idx:02d}-R"
                    needs_change = (rp.name != new_name) or (clear_rear_label and (rp.label or ""))

                    rp.name = new_name
                    if clear_rear_label:
                        rp.label = ""  # rear label not needed

                    if needs_change:
                        _validated_save(rp)
                        self.log_success(f"RearPort -> {new_name}", obj=rp)
                    else:
                        self.log_info(f"RearPort already correct -> {new_name}", obj=rp)

            if not commit:
                self.log_warning("Dry-run only (commit=False). No changes were saved.")

        self.log_success(
            f"Completed. Devices processed: {touched} | "
            f"Skipped (no 'Patch Panel'): {skipped_no_phrase} | "
            f"Skipped (no identifier after phrase): {skipped_no_id} | "
            f"Skipped (no ports): {skipped_no_ports}"
        )
