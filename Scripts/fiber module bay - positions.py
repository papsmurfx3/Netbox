from extras.scripts import Script, ObjectVar, BooleanVar
from dcim.models import DeviceType, Device, ModuleBay

class FixModuleBayPositionsByDeviceType(Script):
    class Meta:
        name = "Fix Module Bay Positions by DeviceType"
        description = (
            "Populate missing ModuleBay.position values for ALL devices of a selected DeviceType.\n"
            "Intended for bays named A, B, C... so ModuleType placeholder {module} works."
        )
        commit_default = False  # start in dry-run mode

    device_type = ObjectVar(
        model=DeviceType,
        required=True,
        description="Select the DeviceType to target (e.g., 'Opticom FMD1 (Legacy 3-Port)').",
    )

    only_missing = BooleanVar(
        required=False,
        default=True,
        description="Only set position where it is currently blank/null (recommended).",
    )

    def run(self, data, commit):

        # Map bay letters to numeric positions (supports A..Z; commonly panels skip I, but include it here)
        letter_order = ["A","B","C","D","E","F","G","H","I","J","K","L","M","N","O","P","Q","R","S","T","U","V","W","X","Y","Z"]
        letter_map = {letter: idx + 1 for idx, letter in enumerate(letter_order)}

        dt = data["device_type"]
        devices = Device.objects.filter(device_type=dt)

        self.log_info(f"DeviceType: {dt.manufacturer.name} {dt.model}")
        self.log_info(f"Devices found: {devices.count()}")

        if devices.count() == 0:
            self.log_warning("No devices found for this DeviceType. Nothing to do.")
            return "No devices processed"

        bays = ModuleBay.objects.filter(device__in=devices).select_related("device")

        if data.get("only_missing", True):
            # cover both NULL and empty string
            bays = bays.filter(position__isnull=True) | bays.filter(position="")

        self.log_info(f"Module bays in scope (missing positions): {bays.count()}")

        updated = 0
        skipped = 0
        warned = 0

        for bay in bays:
            bay_name = (bay.name or "").strip().upper()

            if bay_name not in letter_map:
                warned += 1
                self.log_warning(
                    f"SKIP: Device={bay.device.name} | ModuleBay name='{bay.name}' "
                    f"not a simple letter A-Z; cannot derive position."
                )
                continue

            new_pos = str(letter_map[bay_name])

            # If only_missing=False, still avoid overwriting a filled position unless you want to change that behavior.
            if bay.position not in (None, ""):
                skipped += 1
                continue

            bay.position = new_pos
            bay.save()

            updated += 1
            self.log_success(
                f"UPDATED: Device={bay.device.name} | Bay={bay.name} -> position={bay.position}"
            )

        self.log_info("Summary:")
        self.log_info(f"  Updated:  {updated}")
        self.log_info(f"  Skipped:  {skipped}")
        self.log_info(f"  Warnings: {warned}")

        if not commit:
            self.log_warning(
                "Dry-run unless you enabled 'Commit changes' when launching the script."
            )

        return "Completed"
