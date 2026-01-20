from extras.scripts import Script, MultiObjectVar, BooleanVar
from dcim.models import Device, ModuleBay

class FixModuleBayPositions(Script):
    class Meta:
        name = "Fix Module Bay Positions (Bulk)"
        description = (
            "Populate missing ModuleBay.position values on existing devices.\n"
            "Required for ModuleType placeholder {module} to work when installing modules."
        )
        commit_default = False  # default to dry-run unless user commits

    devices = MultiObjectVar(
        model=Device,
        required=False,
        description=(
            "Optional: choose specific devices. If empty, script will process ALL devices.\n"
            "Recommendation: start with 1-2 devices as a test."
        ),
    )

    only_missing = BooleanVar(
        required=False,
        default=True,
        description="If checked, update ONLY bays where position is currently blank/null (recommended).",
    )

    def run(self, data, commit):

        # Panduit-style bay naming often skips 'I'. Customize if your bays differ.
        # If your bays are numeric already (1..12), the script will use that too.
        letter_order = ["A", "B", "C", "D", "E", "F", "G", "H", "J", "K", "L", "M", "N", "O", "P", "Q",
                        "R", "S", "T", "U", "V", "W", "X", "Y", "Z"]
        letter_map = {letter: idx + 1 for idx, letter in enumerate(letter_order)}

        # Decide device scope
        selected_devices = data.get("devices")
        if selected_devices:
            device_qs = Device.objects.filter(pk__in=[d.pk for d in selected_devices])
            self.log_info(f"Processing {device_qs.count()} selected device(s).")
        else:
            device_qs = Device.objects.all()
            self.log_info(f"No devices selected; processing ALL devices ({device_qs.count()}).")

        changed = 0
        skipped = 0
        warned = 0

        # Pre-filter bays for performance
        bay_qs = ModuleBay.objects.filter(device__in=device_qs).select_related("device")

        if data.get("only_missing", True):
            # Position may be NULL or empty string depending on data history
            bay_qs = bay_qs.filter(position__isnull=True) | bay_qs.filter(position="")

        total = bay_qs.count()
        self.log_info(f"Module bays in scope: {total}")

        for bay in bay_qs:
            current_pos = bay.position

            # If only_missing isn't set, we still avoid overwriting a valid position
            if current_pos not in (None, ""):
                skipped += 1
                continue

            # Derive a position from bay.name or bay.label
            # Priority: numeric -> letter map -> skip with warning
            name = (bay.name or "").strip()
            label = (bay.label or "").strip() if hasattr(bay, "label") else ""

            derived_pos = None

            # 1) numeric bay name like "1", "2", ...
            if name.isdigit():
                derived_pos = int(name)

            # 2) numeric label like "1"
            elif label.isdigit():
                derived_pos = int(label)

            # 3) letter bay name like "A", "B", ...
            elif name.upper() in letter_map:
                derived_pos = letter_map[name.upper()]

            # 4) letter label like "A"
            elif label.upper() in letter_map:
                derived_pos = letter_map[label.upper()]

            if derived_pos is None:
                warned += 1
                self.log_warning(
                    f"SKIP: Device={bay.device.name} | ModuleBay='{bay.name}' (label='{label}') "
                    f"has no numeric/recognized letter to derive a position."
                )
                continue

            # Apply
            bay.position = str(derived_pos)  # NetBox stores position as a string in many cases
            bay.save()

            changed += 1
            self.log_success(
                f"UPDATED: Device={bay.device.name} | ModuleBay='{bay.name}' (label='{label}') "
                f"-> position={bay.position}"
            )

        self.log_info("Summary:")
        self.log_info(f"  Updated: {changed}")
        self.log_info(f"  Skipped: {skipped}")
        self.log_info(f"  Warnings (could not derive): {warned}")

        if not commit:
            self.log_warning(
                "This run may be a DRY RUN depending on the commit toggle. "
                "If you want changes saved, enable 'Commit changes' when running the script."
            )

        return "Completed"
