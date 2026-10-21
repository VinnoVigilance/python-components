import hashlib
import json
import os
import re
from string import Formatter
from urllib.parse import unquote, urlparse

from nameparser import HumanName


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


    def merge_un_designated_vessel_rows(self, records, config):
        """Group UN 1718 PDF rows into one record per vessel."""

        def clean(value):
            if value is None:
                return ""
            return re.sub(r"\s+", " ", str(value)).strip()

        def split_numbered(value):
            parts = re.split(
                r"(?:^|\s)\d+\s*[:.]\s*",
                clean(value)
            )
            result = []
            seen = set()

            for part in parts:
                part = clean(part)

                if part and part.casefold() not in seen:
                    result.append(part)
                    seen.add(part.casefold())

            return result

        detail_fields = {
            "flag": "FlagRaw",
            "call sign": "CallSignRaw",
            "type": "VesselType",
            "other information": "OtherInformation",
            "picture link": "PictureLinkRaw",
            "designated as economic resources of": "EconomicResourceOf",
        }

        required_fields = {
            "RecordNumber",
            "VesselNameOrDetailLabel",
            "DetailValue",
            "IMOAndMMSI",
            "DesignationDate",
        }

        vessels = []
        current = None
        seen_numbers = set()

        for row_index, row in enumerate(records, start=1):
            if not isinstance(row, dict):
                raise ValueError(
                    f"UN vessels row {row_index} is not a dictionary."
                )

            if not required_fields.issubset(row):
                raise ValueError(
                    f"UN vessels row {row_index} has unexpected columns."
                )

            number = clean(row.get("RecordNumber"))
            label = clean(row.get("VesselNameOrDetailLabel"))

            if number:
                if not re.fullmatch(r"\d+", number):
                    raise ValueError(
                        f"Invalid UN vessel record number: {number}"
                    )

                if number in seen_numbers:
                    raise ValueError(
                        f"Duplicate UN vessel record number: {number}"
                    )

                names = split_numbered(label)

                if not names:
                    raise ValueError(
                        f"UN vessel {number} has no name."
                    )

                seen_numbers.add(number)

                current = dict(row)
                current["source_rows"] = [dict(row)]
                current["VesselName"] = names[0]
                current["VesselNames"] = [
                    {
                        "Name": name,
                        "NameType": (
                            "Primary Name" if index == 0 else "Alias"
                        ),
                    }
                    for index, name in enumerate(names)
                ]

                for field in detail_fields.values():
                    current[field] = None

                vessels.append(current)
                continue

            if not any(
                clean(row.get(field))
                for field in required_fields
            ):
                continue

            output_field = detail_fields.get(label.casefold())

            if current is None or output_field is None:
                raise ValueError(
                    f"Unexpected UN vessel detail at row "
                    f"{row_index}: {label!r}"
                )

            current["source_rows"].append(dict(row))
            value = clean(row.get("DetailValue"))

            # Join PDF line breaks inside picture URLs.
            if (
                output_field == "PictureLinkRaw"
                and value.lower().startswith(("http://", "https://"))
            ):
                value = re.sub(r"\s+", "", value)

            previous = current[output_field]

            if value:
                current[output_field] = (
                    f"{previous}\n{value}" if previous else value
                )

        if not vessels:
            raise ValueError(
                "No numbered UN vessel records were found."
            )

        seen_ids = set()

        for vessel in vessels:
            identifiers = clean(vessel.get("IMOAndMMSI"))
            imo_match = re.match(r"^(\d{7})(?!\d)", identifiers)
            imo = imo_match.group(1) if imo_match else None

            if (
                imo is None
                and identifiers.casefold() != "does not exist"
            ):
                raise ValueError(
                    f"Unexpected IMO value for vessel "
                    f"{vessel['RecordNumber']}: {identifiers!r}"
                )

            mmsi_parts = re.split(
                r"\bMMSI\s*:",
                identifiers,
                maxsplit=1,
                flags=re.IGNORECASE
            )

            vessel["IMO"] = imo
            vessel["MMSI"] = (
                list(dict.fromkeys(
                    re.findall(r"\b\d{9}\b", mmsi_parts[1])
                ))
                if len(mmsi_parts) == 2
                else []
            )

            vessel["Flags"] = split_numbered(
                vessel["FlagRaw"]
            )
            vessel["CallSigns"] = split_numbered(
                vessel["CallSignRaw"]
            )

            if imo:
                source_id = f"UN-1718-VESSEL-IMO-{imo}"
            else:
                name_key = vessel["VesselName"].casefold()
                name_hash = hashlib.sha256(
                    name_key.encode("utf-8")
                ).hexdigest()

                source_id = f"UN-1718-VESSEL-NAME-{name_hash}"

            if source_id in seen_ids:
                raise ValueError(
                    f"Duplicate UN vessel source identifier: {source_id}"
                )

            seen_ids.add(source_id)
            vessel["source_record_id"] = source_id

        for vessel in vessels:
            source_rows = vessel.get("source_rows")

            if not isinstance(source_rows, list) or not source_rows:
                raise ValueError(
                    "UN vessel record must contain non-empty source_rows."
                )

            measure_definitions = {
                "Measure_1718_8d_2270_12": (
                    "Asset Freeze",
                    "Asset freeze under paragraph 8(d) of resolution "
                    "1718 (2006) and paragraph 12 of resolution 2270 (2016).",
                ),
                "Measure_2321_12a": (
                    "Maritime Restriction",
                    "De-flagging under paragraph 12(a) of resolution "
                    "2321 (2016).",
                ),
                "Measure_2321_12d": (
                    "Asset Freeze",
                    "Asset freeze under paragraph 12(d) of resolution "
                    "2321 (2016).",
                ),
                "Measure_2371_6_2375_6": (
                    "Maritime Restriction",
                    "Port-entry restriction under paragraph 6 of resolution "
                    "2371 (2017) and paragraph 6 of resolution 2375 (2017).",
                ),
                "Measure_OMM_EconomicResources": (
                    "Asset Freeze",
                    "Listed as an economic resource controlled or operated "
                    "by Ocean Maritime Management (KPe.020); asset freeze "
                    "under paragraph 23 of resolution 2270 (2016).",
                ),
            }

            dates = []
            measures = []
            seen_dates = set()
            seen_measures = set()

            for row in source_rows:
                if not isinstance(row, dict):
                    raise ValueError(
                        "UN vessel source row is not a dictionary."
                    )

                designation_date = clean(
                    row.get("DesignationDate")
                )

                if designation_date.casefold() in {"", "na", "n/a"}:
                    designation_date = ""

                if (
                    designation_date
                    and designation_date not in seen_dates
                ):
                    dates.append({
                        "OriginalValue": designation_date,
                        "Type": "Designation Date",
                    })
                    seen_dates.add(designation_date)

                for field, definition in measure_definitions.items():
                    measure_type, note = definition
                    flag = clean(row.get(field)).casefold()

                    if flag in {"", "na", "n/a"}:
                        continue

                    if flag != "yes":
                        raise ValueError(
                            f"Unexpected UN measure value: "
                            f"{field}={flag!r}"
                        )

                    if not designation_date:
                        raise ValueError(
                            f"UN measure {field} has no designation date."
                        )

                    key = (field, designation_date)

                    if key in seen_measures:
                        continue

                    seen_measures.add(key)
                    measures.append({
                        "MeasureType": measure_type,
                        "EffectiveDate": designation_date,
                        "Note": note,
                    })

            if not dates or not measures:
                raise ValueError(
                    "UN vessel record has no designation dates or measures."
                )

            identifiers = []
            seen_identifiers = set()

            for identifier_type, values in (
                ("IMO Number", [vessel.get("IMO")]),
                ("MMSI", vessel.get("MMSI") or []),
                ("Call Sign", vessel.get("CallSigns") or []),
            ):
                for value in values:
                    number = clean(value)

                    if number.casefold() in {"", "na", "n/a"}:
                        continue

                    key = (identifier_type, number.casefold())

                    if key in seen_identifiers:
                        continue

                    seen_identifiers.add(key)
                    identifiers.append({
                        "Type": identifier_type,
                        "Number": number,
                    })

            aliases = [
                {
                    "Alias": clean(item.get("Name")),
                    "AliasType": "AKA",
                }
                for item in (vessel.get("VesselNames") or [])
                if item.get("NameType") == "Alias"
                and clean(item.get("Name"))
            ]

            seen_alias_names = {
                clean(vessel.get("VesselName")).casefold()
            }
            seen_alias_names.update(
                item["Alias"].casefold()
                for item in aliases
            )

            other_information = clean(
                vessel.get("OtherInformation")
            )

            # Extract names explicitly attributed to this vessel.
            # Example: "now operating as a DPRK flagged vessel named ..."
            for match in re.finditer(
                r"\bnow\s+operating\s+as\s+[^.\n]*?"
                r"\bvessel\s+named\s+(?:the\s+)?([^.\n]+)",
                other_information,
                flags=re.IGNORECASE,
            ):
                for value in re.split(
                    r"\s+or\s+",
                    match.group(1),
                    flags=re.IGNORECASE,
                ):
                    alias = clean(value)
                    alias_key = alias.casefold()

                    if alias_key in {"", "na", "n/a"}:
                        continue

                    if alias_key in seen_alias_names:
                        continue

                    aliases.append({
                        "Alias": alias,
                        "AliasType": "AKA",
                    })
                    seen_alias_names.add(alias_key)

            comments = []

            for field, comment_type in (
                ("OtherInformation", "Other Information"),
                ("EconomicResourceOf", "Designated Economic Resource Of"),
                ("FlagRaw", "Flags As Listed"),
                ("CallSignRaw", "Call Signs As Listed"),
                ("PictureLinkRaw", "Picture Link As Listed"),
            ):
                text = clean(vessel.get(field))

                if text.casefold() not in {"", "na", "n/a"}:
                    comments.append({
                        "type": comment_type,
                        "text": text,
                    })

            vessel["DesignationEntries"] = dates
            vessel["SanctionMeasures"] = measures
            vessel["VesselIdentifiers"] = identifiers
            vessel["VesselAliases"] = aliases
            vessel["VesselComments"] = comments

        return vessels

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

        name = str(record.get(input_field) or "").strip()

        organization_pattern = (
            r"(?i)(?:"
            r"\b(?:"
            r"INC|INCORPORATED|INCORPORATION|"
            r"CORP|CORPORATIONS?|CORPORATIION|"
            r"COMPANY|COMPANIES|LLC|LLP|LTD|LIMITED|OPC|"
            r"GMBH|AG|AB|JSC|PJSC|PTE|PVT|"
            r"OY|OU|TMI|FZE|FZC|SDN|BHD|"
            r"UNIVERSITY|INSTITUTE|ACADEMY|"
            r"FIRMS?|OFFICES?|LAW|LAWFIRM|ASSOCIATES|PARTNERS|"
            r"CPA|CPAS|ACCOUNTING|BOOKKEEPING|"
            r"AUDITING|CONSULTANCY|"
            r"CONSTRUCTION|BUSINESS|GROUP|GRUP|"
            r"TRADING|TRADE|STORES?|SHOPS?|SHOPPES?|"
            r"PAWNSHOP|WORKSHOP|JEWELRY|JEWELLERY|"
            r"JEWELS|MERCHANDISE|MDSE|REALTY|"
            r"BROKERAGE|SERVICES|SYSTEMS|"
            r"AIR|AIRLINES|AIRWAYS|AVIATION|AIRCRAFT|"
            r"TECHNOLOGY|TECHNOLOGIES|TECHNIK|"
            r"TECHNOLOGIC|TECH|"
            r"INTERNATIONAL|COMMUNICATION|"
            r"COMMUNICATIONS|TELECOM|"
            r"ELECTRONICS|ELECTRONIC|"
            r"INDUSTRY|INDUSTRIES|INDUSTRIAL|"
            r"MANUFACTURER|ENGINEERING|"
            r"LOGISTICS|CARGO|TRANSPORT|"
            r"TRANSPORTATION|FORWARDING|"
            r"COURIER|SHIPPING|"
            r"EQUIPMENT|MACHINERY|AUTOMATION|"
            r"COMPUTERS|LAB|LABS|PHOTONICS|"
            r"HARDWARE|SUPPLY|"
            r"EXPORTERS|IMPORTERS|"
            r"VERTRIEBS|ANSTALT"
            r")\b|"
            r"\b(?:L\.L\.C\.?|S\.R\.O\.?|"
            r"A\.G\.|S\.A\.|e\.K\.)(?=\W|$)|"
            r"\bCO\.(?=\W|$)|"
            r"\.NET\b"
            r")"
        )

        if re.search(organization_pattern, name):
            record[output_field] = "Entity"
            return record

        parsed_name = HumanName(name)

        has_person_name = bool(
            parsed_name.first and parsed_name.last
        )

        has_title_and_name = bool(
            parsed_name.title
            and (parsed_name.first or parsed_name.last)
        )

        record[output_field] = (
            "Individual"
            if has_person_name or has_title_and_name
            else "Entity"
        )

        return record

    def set_constant_field(self, record, config):
        """Stamp a fixed value; with `when_field`, only if that field matches `when_pattern` (default: non-blank)."""
        output_field = config["output_field"]
        value = config["value"]
        overwrite = config.get("overwrite", False)

        when_field = config.get("when_field")
        if when_field:
            condition_value = str(self._resolve_field(record, when_field) or "")
            if not re.search(config.get("when_pattern", r"\S"), condition_value):
                return record

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

    def build_url_from_template(self, record, config):
        output_field = config.get(
            "output_field",
            "SourceURL",
        )

        template = config.get(
            "template"
        )

        if not template:
            raise ValueError(
                "build_url_from_template requires template."
            )

        overwrite = config.get(
            "overwrite",
            False,
        )

        current_value = record.get(
            output_field
        )

        if (
            not overwrite
            and current_value is not None
            and str(current_value).strip()
        ):
            return record

        template_fields = {
            field_name
            for _, field_name, _, _
            in Formatter().parse(template)
            if field_name
        }

        missing_fields = [
            field_name
            for field_name in template_fields
            if (
                record.get(field_name) is None
                or str(
                    record.get(field_name)
                ).strip() == ""
            )
        ]

        if missing_fields:
            raise ValueError(
                "Cannot build URL. Missing template "
                f"field(s): {', '.join(sorted(missing_fields))}"
            )

        record[output_field] = (
            template.format_map(record)
        )

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

        name = str(
            self._resolve_field(record, name_field, "")
        ).strip()
        resolution_text = str(
            self._resolve_field(record, resolution_field, "")
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
            "detail.date_and_place_of_birth",
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
            resolved = record

            for part in field.split("."):
                resolved = (
                    resolved.get(part)
                    if isinstance(resolved, dict)
                    else None
                )

            value = str(resolved or "").strip()

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

    def disambiguate_duplicate_keys(self, records, config):
        """Copy base_field to output_field; when the key_fields combination repeats
        in the dataset, append tiebreak_fields (joined by separator) to keep ids unique."""
        key_fields = config["key_fields"]
        base_field = config["base_field"]
        tiebreak_fields = config["tiebreak_fields"]
        output_field = config["output_field"]
        separator = config.get("separator", "|")

        def text(record, field_path):
            return str(self._resolve_field(record, field_path) or "").strip()

        def key_of(record):
            return tuple(text(record, field).upper() for field in key_fields)

        counts = {}

        for record in records:
            key = key_of(record)
            counts[key] = counts.get(key, 0) + 1

        for record in records:
            parts = [text(record, base_field)]

            if counts[key_of(record)] > 1:
                parts.extend(text(record, field) for field in tiebreak_fields)

            record[output_field] = separator.join(parts)

        return records

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
        resolved = record
        for part in config["input_field"].split("."):
            resolved = resolved.get(part) if isinstance(resolved, dict) else None
        value = str(resolved or "").strip()
        match = re.match(config["pattern"], value)

        for group_name, output_field in config["outputs"].items():
            captured = match.group(group_name) if match else None
            record[output_field] = captured.strip() if captured else ""

        return record

    def resolve_reference(self, record, config):
        """
        Resolve a per-record key against a co-located list and project fields.

        Generic keyed lookup: derive a key from the record, find the one item in
        `list_field` whose own key equals it, then copy chosen sub-fields of that
        item onto the record. Any "row references one entry in an attached/shared
        list" shape reuses it by config, not code. Keys can be read verbatim or
        pulled out of free text with a regex, so an id embedded in a label
        ("... *12", "code: 14") works the same as a plain id field. No match
        leaves the outputs at `default` and keeps the record.

        config:
            key_field     dot-path to the value holding the record's key.
            key_pattern   optional regex; group(1) (else whole match) is the key.
            list_field    dot-path to the list of candidate items.
            item_key      sub-field of each item holding its key.
            item_pattern  optional regex applied to item_key before comparing.
            outputs       {item_subfield: output_field} copied from the match.
            default       written to every output when nothing matches ("").
        """

        def dig(obj, path):
            for part in path.split("."):
                obj = obj.get(part) if isinstance(obj, dict) else None
            return obj

        def key_of(text, pattern):
            text = "" if text is None else str(text)
            if not pattern:
                return text.strip()
            found = re.search(pattern, text)
            if not found:
                return ""
            return (found.group(1) if found.groups() else found.group(0)).strip()

        record_key = key_of(dig(record, config["key_field"]), config.get("key_pattern"))
        items = dig(record, config["list_field"]) or []

        chosen = None

        if record_key:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if key_of(item.get(config["item_key"]), config.get("item_pattern")) == record_key:
                    chosen = item
                    break

        default = config.get("default", "")

        for item_subfield, output_field in config["outputs"].items():
            record[output_field] = (chosen or {}).get(item_subfield, default)

        return record

    def normalize_empty_fields(self, record, config):
        """Normalize source empty markers in flat or dotted fields."""

        for field_path in config.get("fields", []):
            parts = field_path.split(".")
            parent = record

            for part in parts[:-1]:
                if not isinstance(parent, dict):
                    parent = None
                    break

                parent = parent.get(part)

            if not isinstance(parent, dict) or parts[-1] not in parent:
                continue

            value = parent.get(parts[-1])
            normalized = "" if value is None else str(value).strip()

            if normalized.upper() in EMPTY_VALUES:
                parent[parts[-1]] = ""

        return record

    @staticmethod
    def _resolve_field(record, field_path, default=None):
        value = record

        for part in str(field_path).split("."):
            if not isinstance(value, dict):
                return default

            value = value.get(part, default)

        return value
