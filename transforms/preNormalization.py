import json
import re
import pandas as pd
from pathlib import Path
from copy import deepcopy


# =========================================================
# Handlers
# =========================================================

class BaseHandler:

    def normalize(self, value, rule):

        raise NotImplementedError


# =========================================================
# Enum Handler
# =========================================================

class EnumHandler(BaseHandler):

    """
    Rule format:

    Entity=Organization|Individual=Individual
    """

    def normalize(self, value, rule):

        if value is None:

            return value

        mapping = {}

        for item in str(rule).split("|"):

            if "=" not in item:
                continue

            k, v = item.split("=", 1)

            mapping[k.strip()] = v.strip()

        return mapping.get(str(value).strip(), value)
    
class RemoveListMarkersHandler(BaseHandler):
    
    def normalize(self, value, rule):

        if value is None:
            return value

        value = str(value)

        value = re.sub(r"\([a-zA-Z]\)\s*", "", value)

        return value.strip()


# =========================================================
# Before Parenthesis Handler
# =========================================================

class BeforeParenthesisHandler(BaseHandler):

    """
    Example:

    Listing Date (EO 14024 Directive 3):
    ->
    Listing Date
    """

    def normalize(self, value, rule):

        if value is None:

            return value

        value = str(value)

        value = value.split("(")[0]

        value = value.replace(":", "")

        return value.strip()
    
# =========================================================
# DATE
# =========================================================
    
class DateFormatHandler(BaseHandler):

    """
    Rule examples:

    MM/DD/YYYY
    DD/MM/YYYY
    """

    def normalize(self, value, rule):

        if value is None:
            return value

        value = str(value).strip()

        if not value:
            return value

        if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value):
            return value

        first, second, year = value.split("/")

        first = int(first)
        second = int(second)

        rule = str(rule).strip().upper()

        # MM/DD/YYYY
        if rule == "MM/DD/YYYY":
            month = first
            day = second

        # DD/MM/YYYY
        elif rule == "DD/MM/YYYY":
            day = first
            month = second

        else:
            return value

        return f"{year}-{month:02}-{day:02}"


# =========================================================
# Regex Extract Handler
# =========================================================

class RegexExtractHandler(BaseHandler):

    """
    Pull a value out of free text using a regex written in the rule cell.

    If the regex has a capture group, group(1) is returned; otherwise the
    whole match. No match returns "". Generic -- any list can point this at
    any field with any regex; nothing here is list-specific.

    Example (SECO vessels):

    field "Other information" = "IMO number: 9037123"
    rule  = (?i)IMO\\s*Numbe?r?\\D*(\\d+)
    ->
    "9037123"
    """

    def normalize(self, value, rule):

        if value is None:
            return ""

        match = re.search(str(rule), str(value))

        if not match:
            return ""

        if match.groups():
            return match.group(1)

        return match.group(0)


# =========================================================
# Handler Registry
# =========================================================

HANDLERS = {
    "enum": EnumHandler(),
    "before_parenthesis": BeforeParenthesisHandler(),
    "remove_list_markers": RemoveListMarkersHandler(),
    "date_format": DateFormatHandler(),
    "regex_extract": RegexExtractHandler(),
}


# =========================================================
# Nested Path Utilities
# =========================================================

def parse_path(path):

    """
    Example:

    relationships.relationship[].type.text

    =>
    [
        ("relationships", False),
        ("relationship", True),
        ("type", False),
        ("text", False),
    ]
    """

    parts = []

    for part in path.split("."):

        if part.endswith("[]"):

            parts.append((part[:-2], True))

        else:

            parts.append((part, False))

    return parts


def get_nested_values(data, path):

    """
    Returns:

    [
        (parent, key, value)
    ]
    """

    parsed = parse_path(path)

    current = [(None, None, data)]

    for key, is_array in parsed:

        next_items = []

        for parent, parent_key, item in current:

            if not isinstance(item, dict):
                continue

            if key not in item:
                continue

            value = item[key]

            # ---------------------------------------------
            # Array
            # ---------------------------------------------

            if is_array:

                if isinstance(value, list):

                    for idx, array_item in enumerate(value):

                        next_items.append(
                            (value, idx, array_item)
                        )

            # ---------------------------------------------
            # Normal field
            # ---------------------------------------------

            else:

                next_items.append(
                    (item, key, value)
                )

        current = next_items

    return current


def set_nested_value(parent, key, value):

    if isinstance(parent, list):

        parent[key] = value

    elif isinstance(parent, dict):

        parent[key] = value


# =========================================================
# Engine
# =========================================================

class PreNormalizationEngine:

    def __init__(
        self,
        prenormalization_df: pd.DataFrame,
        source_config_df: pd.DataFrame,
    ):

        self.prenorm_df = prenormalization_df.fillna("")

        self.source_config_df = source_config_df.fillna("")

        self.source_entity_fields = (
            self._build_source_entity_field_map()
        )

    # -----------------------------------------------------
    # Build source -> entity_field map
    # -----------------------------------------------------

    def _build_source_entity_field_map(self):

        result = {}

        for _, row in self.source_config_df.iterrows():

            source = str(row["source"]).strip()

            entity_field = str(
                row["entity_field"]
            ).strip()

            result[source] = entity_field

        return result
    
    # -----------------------------------------------------
    # Detect Entity Type
    # -----------------------------------------------------

    def detect_entity_type(self, source, raw_json):

        entity_field = self.source_entity_fields.get(source)

        if not entity_field:

            return None

        matches = get_nested_values(
            raw_json,
            entity_field,
        )

        if not matches:

            return None

        raw_entity_value = matches[0][2]

        rules = self.prenorm_df[
            (self.prenorm_df["source"] == source)
            &
            (self.prenorm_df["field"] == entity_field)
            &
            (
                self.prenorm_df["normalization_type"]
                == "enum"
            )
        ]

        if rules.empty:

            return raw_entity_value

        rule_row = rules.iloc[0]

        handler = HANDLERS["enum"]

        normalized_entity = handler.normalize(
            raw_entity_value,
            rule_row["normalization_rule"],
        )

        return normalized_entity

    # -----------------------------------------------------
    # Normalize One Record
    # -----------------------------------------------------

    def pre_normalize_record(self, source, raw_json):

        normalized_json = deepcopy(raw_json)

        # ---------------------------------------------
        # Detect Entity Type
        # ---------------------------------------------

        entity_field = self.source_entity_fields.get(source)

        entity_type = self.detect_entity_type(
            source,
            normalized_json,
        )

        # ---------------------------------------------
        # Overwrite Entity Field
        # ---------------------------------------------

        if entity_field:

            matches = get_nested_values(
                normalized_json,
                entity_field,
            )

            for parent, key, _ in matches:

                set_nested_value(
                    parent,
                    key,
                    entity_type,
                )

        # ---------------------------------------------
        # Stamp the canonical entity_type
        # ---------------------------------------------
        # The mapper reads the resolved type from the single field
        # `entity_type`. Each list declares *where* its type lives via
        # sourceConfig (`entity_field`); once resolved here, we copy it into
        # `entity_type` so routing is general -- a new list needs only a
        # sourceConfig row + an enum rule, never a code change.

        if entity_type:

            normalized_json["entity_type"] = entity_type

        # ---------------------------------------------
        # Load Rules
        # ---------------------------------------------

        rules = self.prenorm_df[
            (self.prenorm_df["source"] == source)
            &
            (
                (
                    self.prenorm_df["entity_type"]
                    == entity_type
                )
                |
                (
                    self.prenorm_df["entity_type"]
                    == "*"
                )
            )
        ]

        # ---------------------------------------------
        # Apply Rules
        # ---------------------------------------------

        for _, rule in rules.iterrows():

            field = str(rule["field"]).strip()

            normalization_type = str(
                rule["normalization_type"]
            ).strip()

            normalization_rule = str(
                rule["normalization_rule"]
            ).strip()

            # -----------------------------------------
            # Handler Exists?
            # -----------------------------------------

            if normalization_type not in HANDLERS:

                print(
                    f"[WARNING] "
                    f"Handler not found: "
                    f"{normalization_type}"
                )

                continue

            handler = HANDLERS[normalization_type]

            # -----------------------------------------
            # Find Matches
            # -----------------------------------------

            matches = get_nested_values(
                normalized_json,
                field,
            )

            if not matches:

                continue

            # -----------------------------------------
            # Apply Normalization
            # -----------------------------------------

            for parent, key, original_value in matches:

                normalized_value = handler.normalize(
                    original_value,
                    normalization_rule,
                )

                set_nested_value(
                    parent,
                    key,
                    normalized_value,
                )

        return normalized_json

    # -----------------------------------------------------
    # Normalize JSONL
    # -----------------------------------------------------

    def normalize_jsonl(
        self,
        source,
        input_jsonl_path,
        output_jsonl_path,
    ):

        input_jsonl_path = Path(input_jsonl_path)

        output_jsonl_path = Path(output_jsonl_path)

        with open(
            input_jsonl_path,
            "r",
            encoding="utf-8",
        ) as infile, open(
            output_jsonl_path,
            "w",
            encoding="utf-8",
        ) as outfile:

            for line in infile:

                line = line.strip()

                if not line:
                    continue

                raw_json = json.loads(line)

                normalized_json = self.pre_normalize_record(
                    source,
                    raw_json,
                )

                outfile.write(
                    json.dumps(
                        normalized_json,
                        ensure_ascii=False,
                    )
                    + "\n"
                )



def run_pre_normalization(
    source,
    input_jsonl_path,
    output_jsonl_path,
    prenormalization_path,
    source_config_path,
):
    prenormalization_df = pd.read_excel(prenormalization_path)
    source_config_df = pd.read_excel(source_config_path)

    engine = PreNormalizationEngine(
        prenormalization_df,
        source_config_df,
    )

    engine.normalize_jsonl(
        source=source,
        input_jsonl_path=input_jsonl_path,
        output_jsonl_path=output_jsonl_path,
    )

    return output_jsonl_path
