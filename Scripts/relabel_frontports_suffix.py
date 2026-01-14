class LabelPorts(Script):
    """Rename front ports and rear ports based on CSV/TSV input, appending '-R' to rear port names."""

    class Meta:
        name = "Label Ports"
        description = (
            "Update front port and rear port names plus label and description "
            "based on device name and front port name. Rear port names are suffixed with '-R'. "
            "Input columns: devicename, device front port name, new port name/label, new description."
        )

    csvdata = TextVar(
        description=(
            "CSV or TSV data: devicename, front port name, new port name/label, new description"
        ),
        required=True,
    )

    def run(self, data, commit):
        raw = data.get("csvdata", "") or ""
        raw = raw.strip()

        if not raw:
            self.log_failure("No CSV/TSV data provided.")
            return

        # Attempt to sniff the delimiter from a sample of the input.
        sample = raw[:1024]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=[",", "\t", ";", "|"])
        except Exception:
            # Default to comma-separated if sniff fails
            dialect = csv.excel

        f = io.StringIO(raw)
        reader = csv.reader(f, dialect)

        updated = 0
        skipped = 0
        for line_num, row in enumerate(reader, start=1):
            # Skip blank lines and header rows
            if not row or all(not cell.strip() for cell in row):
                skipped += 1
                continue
            if len(row) < 4 or row[0].strip().lower() == "devicename":
                skipped += 1
                continue

            # Extract and strip values
            device_name = row[0].strip()
            front_port_name = row[1].strip()
            new_name = row[2].strip()
            new_desc = row[3].strip()

            # Lookup device
            try:
                device = Device.objects.get(name=device_name)
            except Device.DoesNotExist:
                self.log_failure(f"Line {line_num}: Device '{device_name}' not found")
                skipped += 1
                continue

            # Lookup front port
            try:
                fp = FrontPort.objects.get(device=device, name=front_port_name)
            except FrontPort.DoesNotExist:
                self.log_failure(
                    f"Line {line_num}: FrontPort '{front_port_name}' not found on device '{device_name}'"
                )
                skipped += 1
                continue

            # Update front port attributes
            changed = False
            if fp.name != new_name:
                fp.name = new_name
                changed = True
            if fp.label != new_name:
                fp.label = new_name
                changed = True
            if fp.description != new_desc:
                fp.description = new_desc
                changed = True

            if changed:
                try:
                    fp.full_clean()
                    fp.save()
                    updated += 1
                    self.log_success(
                        f"Line {line_num}: Updated front port '{front_port_name}' -> '{new_name}' on '{device_name}'"
                    )
                except Exception as err:
                    self.log_failure(
                        f"Line {line_num}: Error updating front port '{front_port_name}' on '{device_name}': {err}"
                    )
                    skipped += 1
                    continue
            else:
                self.log_info(
                    f"Line {line_num}: No changes needed for front port '{front_port_name}' on '{device_name}'"
                )

            # Update rear port name only (append '-R' suffix)
            rear_port = getattr(fp, "rear_port", None)
            if rear_port:
                rear_name = f"{new_name}-R"
                if rear_port.name != rear_name:
                    try:
                        rear_port.name = rear_name
                        rear_port.full_clean()
                        rear_port.save()
                        self.log_success(
                            f"Line {line_num}: Updated rear port name to '{rear_name}' on '{device_name}'"
                        )
                    except Exception as err:
                        self.log_failure(
                            f"Line {line_num}: Error updating rear port name on '{device_name}': {err}"
                        )
                else:
                    self.log_info(
                        f"Line {line_num}: Rear port already named '{rear_name}' on '{device_name}'"
                    )
            else:
                # No rear port associated with this front port
                self.log_info(
                    f"Line {line_num}: Front port '{front_port_name}' on '{device_name}' has no associated rear port"
                )

        self.log_info(f"Processing complete. Updated: {updated}, Skipped: {skipped}")