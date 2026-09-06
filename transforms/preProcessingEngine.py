import hashlib
import json
import os
import re
from urllib.parse import unquote, urlparse

from nameparser import HumanName
from scrapy import Selector


# "NA" is intentionally excluded: it is Namibia's ISO country code, not a
# "nothing here" marker. Sources that mean empty use "N/A".
EMPTY_VALUES = {"", "N/A", "NONE", "NULL", "UNKNOWN", "-"}


class PreProcessingEngine:

    def preprocess(self, records, rules):
        records = list(records)

        if not rules:
            return records

        for rule in rules:
            if rule.get("level", "record") != "dataset":
                continue

            handler_name = rule["handler"]
            config = rule.get("config", {})
            handler = getattr(self, handler_name, None)

            if handler is None:
                raise ValueError(
                    f"No preprocessing handler found: {handler_name}"
                )

            records = handler(records, config)

        processed_records = []

        for record in records:
            for rule in rules:
                if rule.get("level", "record") != "record":
                    continue

                handler_name = rule["handler"]
                config = rule.get("config", {})
                handler = getattr(self, handler_name, None)

                if handler is None:
                    raise ValueError(
                        f"No preprocessing handler found: {handler_name}"
                    )

                record = handler(record, config)

            processed_records.append(record)

        return processed_records

    def fix_eu_vessel_multiline_rows(self, records, config):
        fixed_records = []

        for record in records:
            vessel_name_key = next(
                (
                    key
                    for key in record
                    if str(key).startswith("Vessel name at designation time")
                ),
                "Vessel name at designation time",
            )
            vessel_name = str(record.get(vessel_name_key, "")).strip()

            imo_number = str(
                record.get("IMO number", "")
            ).strip()

            date_value = str(
                record.get("Date of application", "")
            ).strip()

            link_value = str(
                record.get(
                    "Link to relevant EU Official Journal ",
                    "",
                )
            ).strip()

            if not vessel_name or not imo_number:
                continue

            if date_value == "#REF!":
                date_value = ""

            if link_value == "#REF!":
                link_value = ""

            record["Vessel name at designation time"] = vessel_name
            record["IMO number"] = imo_number
            record["Date of application"] = date_value
            record[
                "Link to relevant EU Official Journal "
            ] = link_value

            fixed_records.append(record)

        return fixed_records

    def merge_dfat_split_records(self, records, config):
        grouped = {}

        single_value_fields = {
            "Reference",
            "Type",
            "Control Date",
            "Targeted Financial Sanction",
            "Travel Ban",
            "Arms Embargo",
            "Maritime Restriction",
        }

        for record in records:
            reference = str(
                record.get("Reference", "")
            ).strip()

            match = re.match(r"^(\d+)", reference)
            base_reference = (
                match.group(1) if match else reference
            )

            if base_reference not in grouped:
                grouped[base_reference] = {
                    "Reference": base_reference,
                    "Names": [],
                }

            merged = grouped[base_reference]

            name_value = str(
                record.get(
                    "Name of Individual or Entity",
                    "",
                )
            ).strip()

            name_type = str(
                record.get("Name Type", "")
            ).strip()

            alias_strength = str(
                record.get("Alias Strength", "")
            ).strip()

            if name_value:
                name_item = {
                    "Name of Individual or Entity": name_value,
                    "Name Type": name_type,
                    "Alias Strength": alias_strength,
                }

                if name_item not in merged["Names"]:
                    merged["Names"].append(name_item)

            ignored_fields = {
                "Reference",
                "Name of Individual or Entity",
                "Name Type",
                "Alias Strength",
            }

            for field, value in record.items():
                if field in ignored_fields:
                    continue

                value = str(value).strip()

                if value.upper() in EMPTY_VALUES:
                    continue

                if field in single_value_fields:
                    if field not in merged:
                        merged[field] = value
                    continue

                if field not in merged:
                    merged[field] = []

                if value not in merged[field]:
                    merged[field].append(value)

        return list(grouped.values())

    def enrich_atc_profile_data(self, record, config):
        profile_dir = config.get(
            "profile_dir",
            "downloads/profiles",
        )

        images_dir = config.get(
            "images_dir",
            "downloads/images",
        )

        detail_url = str(
            record.get("detail_url", "")
        ).strip()

        if not detail_url:
            return record

        slug = detail_url.rstrip("/").split("/")[-1]
        file_base_name = slug.replace("-", " ").upper()

        profile_file_name = (
            f"{file_base_name} _ Anti-Terrorism Council.html"
        )

        profile_file = os.path.join(
            profile_dir,
            profile_file_name,
        )

        if not os.path.exists(profile_file):
            return record

        with open(
            profile_file,
            "r",
            encoding="utf-8",
        ) as file:
            html = file.read()

        selector = Selector(text=html)
        profile_fields = {}

        rows = selector.xpath("//article//table//tr")

        for row in rows:
            key = row.xpath("./td[1]//text()").getall()
            value = row.xpath("./td[2]//text()").getall()

            key = " ".join(key).strip()
            value = " ".join(value).strip()

            if not key:
                continue

            profile_fields[key] = value

        image_urls = selector.xpath(
            "//article//img/@src"
        ).getall()

        local_images = []

        if os.path.exists(images_dir):
            for file_name in os.listdir(images_dir):
                image_name = os.path.splitext(
                    file_name
                )[0].lower()

                if image_name == slug.lower():
                    local_images.append(
                        os.path.join(
                            images_dir,
                            file_name,
                        )
                    )

        record["profile_data"] = {
            "profile_file": profile_file,
            "profile_slug": slug,
            "profile_fields": profile_fields,
            "image_urls": image_urls,
            "local_images": local_images,
        }

        return record

    def enrich_from_attachment(self, record, config):
        """Load a per-record detail file (keyed off a stub field) and return the
        list_detail shape ``{source_record_id, list, detail}``, so mapping reads
        ``list.*``/``detail.*``. Unchanged if the key or detail file is missing.

        config: attachments_dir, key_field, filename_template (default
        "{key}.json"), id_field/list_field/detail_field."""

        attachments_dir = config.get("attachments_dir")
        key_field = config.get("key_field")

        if not attachments_dir or not key_field:
            return record

        key = str(record.get(key_field, "")).strip()

        if not key:
            return record

        safe_key = key

        for char in '/\\:*?"<>|':
            safe_key = safe_key.replace(char, "-")

        filename = config.get(
            "filename_template", "{key}.json"
        ).format(key=safe_key)

        file_path = os.path.join(attachments_dir, filename)

        if not os.path.exists(file_path):
            return record

        with open(file_path, "r", encoding="utf-8") as handle:
            detail = json.load(handle)

        return {
            config.get("id_field", "source_record_id"): key,
            config.get("list_field", "list"): record,
            config.get("detail_field", "detail"): detail,
        }

    def detect_entity_type(self, record, config):
        input_field = config["input_field"]
        output_field = config.get(
            "output_field",
            "detected_entity_type",
        )

        name = str(record.get(input_field, "")).strip()
        name_upper = name.upper()

        org_keywords = [
            "INC",
            "CORP",
            "COMPANY",
            "CO.",
            "LLC",
            "LTD",
            "OPC",
            "SERVICES",
            "FIRM",
            "OFFICE",
            "ASSOCIATES",
            "PARTNERS",
            "CPA",
            "CPAS",
            "ACCOUNTING",
            "BOOKKEEPING",
            "AUDITING",
            "CONSULTANCY",
            "BUSINESS",
            "GROUP",
            "TRADING",
            "STORE",
            "SHOP",
            "REALTY",
            "BROKERAGE",
        ]

        if any(
            keyword in name_upper
            for keyword in org_keywords
        ):
            record[output_field] = "Entity"
            return record

        parsed_name = HumanName(name)

        if parsed_name.first and parsed_name.last:
            record[output_field] = "Individual"
        else:
            record[output_field] = "Entity"

        return record

    def set_constant_field(self, record, config):
        output_field = config["output_field"]
        value = config["value"]
        overwrite = config.get("overwrite", False)

        current_value = record.get(output_field)

        is_empty = (
            current_value is None
            or (
                isinstance(current_value, str)
                and not current_value.strip()
            )
        )

        if overwrite or is_empty:
            record[output_field] = value

        return record

    def generate_atc_unique_id(self, record, config):
        name_field = config.get("name_field", "name")

        resolution_field = config.get(
            "resolution_field",
            "atc_resolution_no",
        )

        output_field = config.get(
            "output_field",
            "unique_id",
        )

        prefix = config.get("prefix", "ATC")

        name = str(record.get(name_field, "")).strip()
        resolution_text = str(
            record.get(resolution_field, "")
        ).strip()

        match = re.search(
            r"Resolution\s+No\.?\s*([0-9]+)",
            resolution_text,
            re.IGNORECASE,
        )

        resolution_no = (
            match.group(1) if match else "UNKNOWN"
        )

        name_hash = hashlib.md5(
            name.lower().encode("utf-8")
        ).hexdigest()[:10]

        record[output_field] = (
            f"{prefix}-{resolution_no}-{name_hash}"
        )

        return record

    def extract_name_from_url(self, record, config):
        input_field = config.get(
            "input_field",
            "detail_url",
        )

        output_field = config.get(
            "output_field",
            "extracted_name_from_url",
        )

        detail_url = str(
            record.get(input_field, "")
        ).strip()

        if not detail_url:
            record[output_field] = ""
            return record

        path = urlparse(detail_url).path.strip("/")
        slug = path.split("/")[-1]

        record[output_field] = unquote(slug).strip()

        return record

    def split_atc_date_and_place_of_birth(
        self,
        record,
        config,
    ):
        input_field = config.get(
            "input_field",
            "profile_data.profile_fields.Date and Place of Birth",
        )

        date_output_field = config.get(
            "date_output_field",
            "atc_birth_date",
        )

        place_output_field = config.get(
            "place_output_field",
            "atc_birth_place",
        )

        value = record

        for part in input_field.split("."):
            if not isinstance(value, dict):
                value = ""
                break

            value = value.get(part, "")

        value = str(value).strip()

        if value.upper() in EMPTY_VALUES:
            record[date_output_field] = ""
            record[place_output_field] = ""
            return record

        parts = value.split(",", 1)
        first_part = parts[0].strip()
        remaining_part = (
            parts[1].strip() if len(parts) > 1 else ""
        )

        if first_part.upper() in EMPTY_VALUES:
            record[date_output_field] = ""
            record[place_output_field] = remaining_part
            return record

        if re.search(r"\d", first_part):
            record[date_output_field] = first_part
            record[place_output_field] = remaining_part
        else:
            record[date_output_field] = ""
            record[place_output_field] = value

        return record

    def clean_atc_profile_name_fields(
        self,
        record,
        config,
    ):
        fields = config.get("fields", [])

        profile_fields = (
            record.get("profile_data", {})
            .get("profile_fields", {})
        )

        for field in fields:
            value = str(
                profile_fields.get(field, "")
            ).strip()

            if value.upper() in EMPTY_VALUES:
                profile_fields[field] = ""

        return record
    
    def filter_missing_required_field(self, records, config):
        required_field = config["field"]

        valid_records = []
        rejected_count = 0

        for record_index, record in enumerate(records, start=1):
            field_value = str(
                record.get(required_field, "")
            ).strip()

            if not field_value:
                rejected_count += 1

                print(
                    f"[WARNING] Record {record_index} skipped: "
                    f"{required_field} is missing."
                )

                continue

            valid_records.append(record)

        print(
            f"[VALIDATION] {rejected_count} invalid records skipped. "
            f"{len(valid_records)} valid records remaining."
        )

        return valid_records

    def generate_composite_id(
        self,
        record,
        config,
    ):
        fields = config["fields"]

        output_field = config.get(
            "output_field",
            "unique_id",
        )

        prefix = config.get(
            "prefix",
            "",
        )

        values = []

        for field in fields:
            value = str(
                record.get(field, "")
            ).strip()

            values.append(value.upper())

        raw_id = "|".join(values)

        digest = hashlib.sha256(
            raw_id.encode("utf-8")
        ).hexdigest()

        if prefix:
            record[output_field] = (
                f"{prefix}-{digest}"
            )
        else:
            record[output_field] = digest

        return record

    def explode_nested_records(self, records, config):
        """
        Fan a nested list out into one record per item (dataset-level).

        Turns a parent that carries a nested list (e.g. one election contest
        holding many candidates) into many flat records -- one per child --
        so each child can become its own watchlist member downstream. Nothing
        here is source-specific: any "parent object -> nested list of
        children" shape reuses it by writing config, not code.

        config:
            match_field   only explode parents whose match_field equals
                          match_value; omit to explode every parent.
            match_value   the value match_field must equal (compared as text).
            list_path     dot-path to the child list inside each parent,
                          e.g. "candidates.candidates".
            carry_fields  {source_dot_path: output_field} -- values read from
                          the parent by dot-path and copied onto every child,
                          so parent-level context (contest code, statistics)
                          rides along with each exploded record.

        A child that is not a dict is wrapped as {"value": child}. carry_fields
        use setdefault, so a child that already holds the key keeps its value.
        """
        match_field = config.get("match_field")
        match_value = config.get("match_value")
        list_path = config["list_path"]
        carry_fields = config.get("carry_fields", {})

        exploded = []

        for record in records:
            if (
                match_field is not None
                and str(record.get(match_field, "")) != str(match_value)
            ):
                continue

            children = record

            for part in list_path.split("."):
                children = (
                    children.get(part, {})
                    if isinstance(children, dict)
                    else {}
                )

            if not isinstance(children, list):
                continue

            carried = {}

            for source_path, output_field in carry_fields.items():
                value = record

                for part in source_path.split("."):
                    value = (
                        value.get(part)
                        if isinstance(value, dict)
                        else None
                    )

                carried[output_field] = value

            for child in children:
                new_record = (
                    dict(child)
                    if isinstance(child, dict)
                    else {"value": child}
                )

                for key, value in carried.items():
                    new_record.setdefault(key, value)

                exploded.append(new_record)

        return exploded

    def split_field_regex(self, record, config):
        """
        Split one field into several sibling fields via a named-group regex.

        The record-level counterpart of pre-normalization's SplitPatternHandler:
        instead of emitting a list of objects for an array field, it writes each
        captured group onto the SAME record as a plain field. Use it when a
        source packs several values into one string
        ("66. VILLAR, CAMILLE (NP)" -> ballot number, name, party) and those
        parts are needed early -- e.g. the ballot number becomes the record's
        external id, which must exist before the raw member is stored (so this
        cannot wait for pre-normalization). Generic: the regex and the
        group->field mapping live in config, so any packed-string field is
        handled by config, not code.

        config:
            input_field   field to read and split.
            pattern       regex with (?P<name>...) named groups.
            outputs       {group_name: output_field}. A group that did not
                          match (optional group, or no overall match) writes ""
                          so the output field always exists.
        """
        value = str(record.get(config["input_field"], "")).strip()
        match = re.match(config["pattern"], value)

        for group_name, output_field in config["outputs"].items():
            captured = match.group(group_name) if match else None
            record[output_field] = captured.strip() if captured else ""

        return record