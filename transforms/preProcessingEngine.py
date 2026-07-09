import re
import hashlib
from urllib.parse import urlparse, unquote

from nameparser import HumanName


EMPTY_VALUES = {"", "N/A", "NA", "NONE", "NULL", "UNKNOWN", "-"}


class PreProcessingEngine:

    def apply_dataset(self, records, rules):
        if not rules:
            return records

        for rule in rules:
            if rule.get("level", "record") != "dataset":
                continue

            handler_name = rule["handler"]
            handler = getattr(self, handler_name, None)

            if handler is None:
                raise ValueError(
                    f"No preprocessing handler found: {handler_name}"
                )

            records = handler(records)

        return records

    def apply_record(self, record, rules):
        if not rules:
            return record

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

        return record

    def merge_dfat_split_records(self, records):
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
            reference = str(record.get("Reference", "")).strip()

            match = re.match(r"^(\d+)", reference)
            base_reference = match.group(1) if match else reference

            if base_reference not in grouped:
                grouped[base_reference] = {
                    "Reference": base_reference,
                    "Names": []
                }

            merged = grouped[base_reference]

            name_value = str(record.get("Name of Individual or Entity", "")).strip()
            name_type = str(record.get("Name Type", "")).strip()
            alias_strength = str(record.get("Alias Strength", "")).strip()

            if name_value:
                name_item = {
                    "Name of Individual or Entity": name_value,
                    "Name Type": name_type,
                    "Alias Strength": alias_strength
                }

                if name_item not in merged["Names"]:
                    merged["Names"].append(name_item)

            for field, value in record.items():
                if field in {
                    "Reference",
                    "Name of Individual or Entity",
                    "Name Type",
                    "Alias Strength"
                }:
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
    
    def detect_entity_type(self, record, config):
        input_field = config["input_field"]
        output_field = config.get(
            "output_field",
            "detected_entity_type"
        )

        name = str(record.get(input_field, "")).strip()
        name_upper = name.upper()

        org_keywords = [
            "INC", "CORP", "COMPANY", "CO.", "LLC", "LTD", "OPC",
            "SERVICES", "FIRM", "OFFICE", "ASSOCIATES", "PARTNERS",
            "CPA", "CPAS", "ACCOUNTING", "BOOKKEEPING", "AUDITING",
            "CONSULTANCY", "BUSINESS", "GROUP", "TRADING", "STORE",
            "SHOP", "REALTY", "BROKERAGE"
        ]

        if any(keyword in name_upper for keyword in org_keywords):
            record[output_field] = "Entity"
            return record

        parsed_name = HumanName(name)

        if parsed_name.first and parsed_name.last:
            record[output_field] = "Individual"
        else:
            record[output_field] = "Entity"

        return record

    def generate_atc_unique_id(self, record, config):
        name_field = config.get("name_field", "name")
        resolution_field = config.get(
            "resolution_field",
            "atc_resolution_no"
        )
        output_field = config.get("output_field", "unique_id")
        prefix = config.get("prefix", "ATC")

        name = str(record.get(name_field, "")).strip()
        resolution_text = str(record.get(resolution_field, "")).strip()

        match = re.search(
            r"Resolution\s+No\.?\s*([0-9]+)",
            resolution_text,
            re.IGNORECASE
        )

        resolution_no = match.group(1) if match else "UNKNOWN"

        name_hash = hashlib.md5(
            name.lower().encode("utf-8")
        ).hexdigest()[:10]

        record[output_field] = f"{prefix}-{resolution_no}-{name_hash}"

        return record

    def extract_name_from_url(self, record, config):
        input_field = config.get("input_field", "detail_url")
        output_field = config.get(
            "output_field",
            "extracted_name_from_url"
        )

        detail_url = str(record.get(input_field, "")).strip()

        if not detail_url:
            record[output_field] = ""
            return record

        path = urlparse(detail_url).path.strip("/")
        slug = path.split("/")[-1]

        record[output_field] = unquote(slug).strip()

        return record

    def split_atc_date_and_place_of_birth(self, record, config):
        input_field = config.get(
            "input_field",
            "profile_data.profile_fields.Date and Place of Birth"
        )

        date_output_field = config.get(
            "date_output_field",
            "atc_birth_date"
        )
        place_output_field = config.get(
            "place_output_field",
            "atc_birth_place"
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
        remaining_part = parts[1].strip() if len(parts) > 1 else ""

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

    def clean_atc_profile_name_fields(self, record, config):
        fields = config.get("fields", [])

        profile_fields = (
            record.get("profile_data", {})
            .get("profile_fields", {})
        )

        for field in fields:
            value = str(profile_fields.get(field, "")).strip()

            if value.upper() in EMPTY_VALUES:
                profile_fields[field] = ""

        return record