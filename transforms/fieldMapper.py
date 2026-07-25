import json
import pandas as pd

from dataclasses import dataclass
from typing import Any, Optional, List
from collections import defaultdict

from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent / "data" / "rules"
LOOKUP_FILE = RULES_DIR / "pickLists.xlsx"

_LOOKUP_DF = None
_LOOKUP_CACHE = {}


# =========================================================
# MODEL
# =========================================================

@dataclass
class Rule:
    entity_type: str
    target_path: str
    target_type: str
    group: Optional[str]
    source_type: Optional[str]
    source_value: Optional[str]


# =========================================================
# JSON PATH
# =========================================================

class JsonPath:
    @staticmethod
    def split(path: str) -> List[str]:
        return [p.strip() for p in str(path).split(".") if p.strip()] if path else []

    @staticmethod
    def _as_list(value):
        if value is None:
            return []

        if isinstance(value, list):
            return value

        return [value]

    @staticmethod
    def get(data: Any, path: str) -> Any:
        values = JsonPath.get_all(data, path)

        if not values:
            return None

        return values[0]

    @staticmethod
    def get_all(data: Any, path: str) -> List[Any]:
        if data is None:
            return []

        if not path:
            return JsonPath._as_list(data)

        items = [data]

        for part in JsonPath.split(path):
            is_list = part.endswith("[]")
            key = part[:-2] if is_list else part

            next_items = []

            for item in items:
                candidates = item if isinstance(item, list) else [item]

                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue

                    value = candidate.get(key)

                    if value is None:
                        for candidate_key, candidate_value in candidate.items():
                            if str(candidate_key).strip() == key.strip():
                                value = candidate_value
                                break

                    if value is None:
                        continue

                    if is_list:
                        if isinstance(value, list):
                            next_items.extend(value)
                        else:
                            next_items.append(value)
                    else:
                        next_items.append(value)

            items = next_items

        return items

    @staticmethod
    def set(data: dict, path: str, value: Any):
        if not path:
            return

        clean_path = path.replace("[]", "")
        parts = [p for p in clean_path.split(".") if p]

        current = data

        for i, part in enumerate(parts):
            is_last = i == len(parts) - 1

            if is_last:
                current[part] = value
            else:
                if part not in current or not isinstance(current[part], dict):
                    current[part] = {}

                current = current[part]


# =========================================================
# HELPERS
# =========================================================

def unquote(val: Optional[str]) -> str:
    if val is None:
        return ""

    val = str(val).strip()

    if len(val) >= 2 and val[0] == '"' and val[-1] == '"':
        return val[1:-1]

    return val


def normalize_bool(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"

    return str(v).strip().lower()


def values_equal(a: Any, b: Any) -> bool:
    return normalize_bool(a) == normalize_bool(b)


def get_expected_values(expected_expr: str):
    global _LOOKUP_DF

    expected_expr = unquote(expected_expr).strip()

    if expected_expr.startswith("lookup:"):
        lookup_path = expected_expr.replace("lookup:", "", 1).strip().lower()

        if lookup_path in _LOOKUP_CACHE:
            return _LOOKUP_CACHE[lookup_path]

        if _LOOKUP_DF is None:
            _LOOKUP_DF = pd.read_excel(LOOKUP_FILE)

            required_columns = {"Path", "Value"}
            missing_columns = required_columns - set(_LOOKUP_DF.columns)

            if missing_columns:
                raise ValueError(
                    f"pickLists.xlsx must have these columns: {missing_columns}"
                )

        matched = _LOOKUP_DF[
            _LOOKUP_DF["Path"].astype(str).str.strip().str.lower()
            == lookup_path
        ]

        values = [
            str(x).strip().lower()
            for x in matched["Value"].dropna().tolist()
            if str(x).strip()
        ]

        _LOOKUP_CACHE[lookup_path] = values
        return values

    return [
        str(x).strip().lower()
        for x in expected_expr.split(",")
        if x.strip()
    ]
def normalize_scalar(value: Any, fallback=""):
    if value is None:
        return fallback

    if isinstance(value, list):
        if not value:
            return fallback

        if len(value) == 1:
            return value[0]

    return value


def coerce_to_field_type(value: Any, target_type: Optional[str]) -> Any:
    """Make a value obey the Field Type the mapping declares.

    A field declared as a scalar (e.g. Field Type "string") must never hold a
    list. When a source path resolves to several values -- for example UN
    publishes several <VALUE> under one LAST_DAY_UPDATED, so DateUpdated would
    otherwise become ["2016-10-13", "2020-11-02"] -- keep the first non-empty
    value so the declared type holds. Array fields are left untouched.
    """
    if str(target_type or "").strip().lower() in {"array", "list"}:
        return value

    if isinstance(value, list):
        for item in value:
            if not is_empty_value(item):
                return item
        return ""

    return value


def detect_entity_type(raw_json: dict) -> str:
    val = (
        raw_json.get("entity_type")
        or JsonPath.get(raw_json, "generalInfo.entityType.text")
        or raw_json.get("Type", "")
    )

    val = str(val).strip().lower()

    if val in ["individual", "person"]:
        return "Individual"

    if val in ["entity", "organization", "organisation"]:
        return "Entity"

    if val == "vessel":
        return "Vessel"

    if (
        raw_json.get("IMO number")
        or raw_json.get("Vessel name at designation time")
    ):
        return "Vessel"

    return "Individual"


def default_value(target_type: str):
    t = str(target_type or "").strip().lower()

    if t in ["object", "dict"]:
        return {}

    if t in ["array", "list"]:
        return []

    if t in ["json", "raw"]:
        return None

    return ""


# Words a source uses to say "there is nothing here". They carry no more
# information than an empty cell, and glued into a concatenated string
# they read as data: "Baghdad UNKNOWN". Treated as a missing value so the
# existing "keep the default" guards handle them.
#
# Note: pickLists.xlsx defines "Unknown" as a legitimate value for Gender
# and Measures.Status. No source currently produces it there, but if one
# ever does it will be dropped by this and needs an exception.
# "NA" is deliberately NOT here: it is the ISO 3166 country code for Namibia,
# and treating it as a placeholder would silently drop a real country. Sources
# that mean "not available" use "N/A".
PLACEHOLDER_VALUES = {"N/A", "NONE", "NULL", "UNKNOWN", "-", "--"}


def drop_placeholder(value: Any) -> Any:
    """Turn a source's word for "nothing" into an actual nothing."""
    if isinstance(value, str) and value.strip().upper() in PLACEHOLDER_VALUES:
        return None

    return value


# "NA" is ambiguous: under a name or country field it is a real value (a name
# fragment, or Namibia's ISO 3166 code), but elsewhere it usually means "not
# available". So it is scrubbed everywhere EXCEPT under these field names.
NA_SAFE_FIELD_HINTS = ("name", "alias", "country", "nationalit", "citizenship")


def _is_na_safe_key(key: Any) -> bool:
    lowered = str(key).lower()
    return any(hint in lowered for hint in NA_SAFE_FIELD_HINTS)


def scrub_na_placeholders(value: Any, safe: bool = False) -> Any:
    """Drop a bare "NA" value unless it sits under a name/country-type field.

    ``safe`` becomes True once any ancestor key is name/country-like and stays
    True for everything nested under it, so e.g. Names[].* keeps "NA" while
    Programs[].* does not.
    """
    if isinstance(value, dict):
        return {
            key: scrub_na_placeholders(val, safe or _is_na_safe_key(key))
            for key, val in value.items()
        }

    if isinstance(value, list):
        return [scrub_na_placeholders(item, safe) for item in value]

    if not safe and isinstance(value, str) and value.strip().upper() == "NA":
        return ""

    return value


def strip_anchor_prefix(path: str, anchor: Optional[str]) -> str:
    if not path or not anchor:
        return path

    anchor = anchor.strip()

    variants = {
        anchor,
        anchor.replace("[]", "")
    }

    for a in variants:
        if path == a:
            return ""

        if path.startswith(a + "."):
            return path[len(a) + 1:]

    return path


def resolve_in_context(raw_json: dict, expr: str, item: Any = None, anchor: Optional[str] = None):
    expr = str(expr or "").strip()

    if expr == "":
        return item if item is not None else None

    if item is not None:
        value = drop_placeholder(JsonPath.get(item, expr))

        if value is not None:
            return value

        short_expr = strip_anchor_prefix(expr, anchor)

        if short_expr != expr:
            if short_expr == "":
                return item

            value = drop_placeholder(JsonPath.get(item, short_expr))

            if value is not None:
                return value

    return drop_placeholder(JsonPath.get(raw_json, expr))


def resolve_all_in_context(
    raw_json: dict,
    expr: str,
    item: Any = None,
    anchor: Optional[str] = None
) -> List[Any]:

    expr = str(expr or "").strip()

    if expr == "":
        if item is None:
            return []

        return JsonPath._as_list(item)

    def keep(values):
        return [
            value for value in values
            if drop_placeholder(value) is not None
        ]

    if item is not None:
        values = keep(JsonPath.get_all(item, expr))

        if values:
            return values

        short_expr = strip_anchor_prefix(expr, anchor)

        if short_expr != expr:
            if short_expr == "":
                return JsonPath._as_list(item)

            values = keep(JsonPath.get_all(item, short_expr))

            if values:
                return values

    return keep(JsonPath.get_all(raw_json, expr))


def is_empty_value(v):
    if v is None:
        return True

    if v == "":
        return True

    if v == []:
        return True

    if v == {}:
        return True

    return False


def has_meaningful_data(obj: dict) -> bool:
    return any(not is_empty_value(v) for v in obj.values())


def has_real_source_data(rules: List[Rule], raw_json: dict) -> bool:

    for rule in rules:
        st = (rule.source_type or "").lower()

        # constant خودش داده واقعی از سورس نیست
        if st in ["constant", "raw"]:
            continue

        handler = HANDLERS.get(st)

        if not handler:
            continue

        value = handler.handle(rule, raw_json)

        if not is_empty_value(value):
            return True

    return False


# =========================================================
# HANDLERS
# =========================================================

class BaseHandler:
    def handle(
        self,
        rule: Rule,
        raw_json: dict,
        item: Any = None,
        anchor: Optional[str] = None
    ) -> Any:
        raise NotImplementedError


class PathHandler(BaseHandler):
    def handle(self, rule, raw_json, item=None, anchor=None):
        return resolve_in_context(raw_json, rule.source_value, item, anchor)


class ConstantHandler(BaseHandler):
    def handle(self, rule, raw_json, item=None, anchor=None):
        value = unquote(rule.source_value)

        if value.upper() == "TRUE":
            return "true"

        if value.upper() == "FALSE":
            return "false"

        return value


class RawHandler(BaseHandler):
    def handle(self, rule, raw_json, item=None, anchor=None):
        return raw_json


class ExpandHandler(BaseHandler):
    def handle(self, rule, raw_json, item=None, anchor=None):
        if not rule.source_value:
            return []

        return [
            unquote(x.strip())
            for x in str(rule.source_value).split("|")
            if x.strip()
        ]


class ParallelPathHandler(BaseHandler):
    def handle(self, rule, raw_json, item=None, anchor=None):
        paths = [
            x.strip()
            for x in str(rule.source_value or "").split("|")
            if x.strip()
        ]

        return [
            resolve_in_context(raw_json, p, item, anchor)
            for p in paths
        ]


class PathExpandHandler(BaseHandler):
    def handle(self, rule, raw_json, item=None, anchor=None):
        return resolve_all_in_context(raw_json, rule.source_value, item, anchor)


class ListExpandHandler(BaseHandler):
    """Return primitive list values for expansion into separate group objects.

    This handler is intentionally separate from ``path_expand`` so existing
    object-array mappings keep their current behaviour. It accepts both a
    scalar and a list; a scalar produces one object and a list produces one
    object per value.
    """

    def handle(self, rule, raw_json, item=None, anchor=None):
        if not rule.source_value:
            return []

        values = resolve_all_in_context(
            raw_json,
            rule.source_value,
            item,
            anchor,
        )

        results = []

        for value in values:
            candidates = value if isinstance(value, list) else [value]

            for candidate in candidates:
                candidate = drop_placeholder(candidate)

                if is_empty_value(candidate):
                    continue

                results.append(candidate)

        return results


class ConcatPathHandler(BaseHandler):
    def handle(self, rule, raw_json, item=None, anchor=None):
        if not rule.source_value:
            return ""

        parts = [
            p.strip()
            for p in str(rule.source_value).split("|")
            if p.strip()
        ]

        values = []

        for part in parts:
            # Check if it's a literal string (enclosed in quotes)
            if (part.startswith('"') and part.endswith('"')) or \
               (part.startswith("'") and part.endswith("'")):
                # It's a constant/literal string
                literal_value = part[1:-1]  # Remove the quotes
                if literal_value:
                    values.append(literal_value)
            else:
                # It's a path - resolve it from context
                value = resolve_in_context(raw_json, part, item, anchor)
                
                if value is None:
                    continue
                
                value = str(value).strip()
                
                if value:
                    values.append(value)

        return " ".join(values)


class ExplodeHandler(BaseHandler):
    def handle(self, rule, raw_json, item=None, anchor=None):
        if not rule.source_value:
            return []

        source_expr = str(rule.source_value).strip()
        separator = "|"

        if source_expr.endswith(",,"):
            source_expr = source_expr[:-2].strip()
            separator = ","
        elif "," in source_expr:
            source_expr, separator = source_expr.rsplit(",", 1)
            source_expr = source_expr.strip()
            separator = separator.strip()

        raw_values = resolve_all_in_context(
            raw_json,
            source_expr,
            item,
            anchor
        )

        if not raw_values:
            return []

        results = []

        for raw_value in raw_values:
            if raw_value is None:
                continue

            for x in str(raw_value).split(separator):
                x = x.strip().strip(".")

                if x and x.upper() != "N/A":
                    results.append(x)

        return results

class ConditionalPathHandler(BaseHandler):

    def handle(self, rule, raw_json, item=None, anchor=None):

        if not rule.source_value:
            return None

        blocks = [
            b.strip()
            for b in str(rule.source_value).split("OR")
            if b.strip()
        ]

        for block in blocks:

            parts = [x.strip() for x in block.split("|")]

            if len(parts) != 3:
                continue

            cond_path, cond_value, return_expr = parts
            expected_values = get_expected_values(cond_value)


            if item is not None:
                actual = resolve_in_context(
                    raw_json,
                    cond_path,
                    item=item,
                    anchor=anchor
                )

                actual_value = str(actual or "").strip().lower()

                if actual_value in expected_values:

                    if return_expr.startswith('"') and return_expr.endswith('"'):
                        return unquote(return_expr)

                    return resolve_in_context(
                        raw_json,
                        return_expr,
                        item=item,
                        anchor=anchor
                    )

            if "[]" in cond_path:
                base_path = cond_path.split("[]")[0]
                items = JsonPath.get_all(raw_json, base_path + "[]")

                for current_item in items:

                    short_cond = strip_anchor_prefix(
                        cond_path,
                        base_path + "[]"
                    )

                    short_return = strip_anchor_prefix(
                        return_expr,
                        base_path + "[]"
                    )

                    actual = JsonPath.get(current_item, short_cond)
                    actual_value = str(actual or "").strip().lower()

                    if actual_value in expected_values:

                        if return_expr.startswith('"') and return_expr.endswith('"'):
                            return unquote(return_expr)

                        return JsonPath.get(current_item, short_return)

        return None


class ConditionalExcludePathHandler(BaseHandler):

    def handle(self, rule, raw_json, item=None, anchor=None):

        if not rule.source_value:
            return None

        blocks = [
            b.strip()
            for b in str(rule.source_value).split("OR")
            if b.strip()
        ]

        for block in blocks:

            parts = [x.strip() for x in block.split("|")]

            if len(parts) != 3:
                continue

            cond_path, cond_value, return_expr = parts
            expected_values = get_expected_values(cond_value)

            if item is not None:
                actual = resolve_in_context(
                    raw_json,
                    cond_path,
                    item=item,
                    anchor=anchor
                )

                actual_value = str(actual or "").strip().lower()

                if actual_value and actual_value not in expected_values:

                    if return_expr.startswith('"') and return_expr.endswith('"'):
                        return unquote(return_expr)

                    return resolve_in_context(
                        raw_json,
                        return_expr,
                        item=item,
                        anchor=anchor
                    )

            if "[]" in cond_path:
                base_path = cond_path.split("[]")[0]
                items = JsonPath.get_all(raw_json, base_path + "[]")

                for current_item in items:

                    short_cond = strip_anchor_prefix(
                        cond_path,
                        base_path + "[]"
                    )

                    short_return = strip_anchor_prefix(
                        return_expr,
                        base_path + "[]"
                    )

                    actual = JsonPath.get(current_item, short_cond)
                    actual_value = str(actual or "").strip().lower()

                    if actual_value and actual_value not in expected_values:

                        if return_expr.startswith('"') and return_expr.endswith('"'):
                            return unquote(return_expr)

                        return JsonPath.get(current_item, short_return)

        return None


HANDLERS = {
    "path": PathHandler(),
    "constant": ConstantHandler(),
    "raw": RawHandler(),
    "expand": ExpandHandler(),
    "explode": ExplodeHandler(),
    "parallel_path": ParallelPathHandler(),
    "path_expand": PathExpandHandler(),
    "list_expand": ListExpandHandler(),
    "conditional_path": ConditionalPathHandler(),
    "conditional_exclude_path": ConditionalExcludePathHandler(),
    "concat_path": ConcatPathHandler(),
}


# =========================================================
# SCHEMA BUILDER
# =========================================================

class SchemaBuilder:

    def build(self, rules: List[Rule]) -> dict:
        result = {}

        grouped = defaultdict(list)
        scalars = []

        for rule in rules:
            if rule.group:
                grouped[rule.group].append(rule)
            else:
                scalars.append(rule)

        for rule in scalars:
            self._apply_scalar(result, rule)

        for _, group_rules in grouped.items():
            self._apply_group(result, group_rules)

        return result

    def _apply_scalar(self, result: dict, rule: Rule):
        if not rule.target_path:
            return

        if "[]" not in rule.target_path:
            JsonPath.set(
                result,
                rule.target_path,
                default_value(rule.target_type)
            )
            return

        root = rule.target_path.split("[]")[0]
        leaf = rule.target_path.split("[]")[-1].lstrip(".")

        if not leaf:
            JsonPath.set(
                result,
                root,
                default_value(rule.target_type)
            )
            return

        JsonPath.set(
            result,
            root,
            [{leaf: default_value(rule.target_type)}]
        )

    def _apply_group(self, result: dict, rules: List[Rule]):
        if not rules:
            return

        root = rules[0].target_path.split("[]")[0]

        obj = {}

        for rule in rules:
            leaf = rule.target_path.split("[]")[-1].lstrip(".")

            if leaf:
                obj[leaf] = default_value(rule.target_type)

        JsonPath.set(result, root, [obj] if obj else [])


# =========================================================
# GROUP PROCESSOR
# =========================================================

class GroupProcessor:


    def process(self, rules: List[Rule], raw_json: dict) -> List[dict]:
    
        if any("*" in str(r.source_type or "") for r in rules):

            type_parts_list = []
            value_parts_list = []

            max_count = 1

            for r in rules:
                type_parts = [
                    x.strip()
                    for x in str(r.source_type or "").split("*")
                    if x.strip()
                ]

                value_parts = [
                    x.strip()
                    for x in str(r.source_value or "").split("*")
                    if x.strip()
                ]

                if not type_parts:
                    type_parts = [r.source_type]

                if not value_parts:
                    value_parts = [r.source_value]

                type_parts_list.append(type_parts)
                value_parts_list.append(value_parts)

                max_count = max(max_count, len(type_parts), len(value_parts))

            all_items = []

            for i in range(max_count):

                sub_rules = []

                for idx, r in enumerate(rules):
                    type_parts = type_parts_list[idx]
                    value_parts = value_parts_list[idx]

                    sub_rule = Rule(
                        entity_type=r.entity_type,
                        target_path=r.target_path,
                        target_type=r.target_type,
                        group=r.group,
                        source_type=type_parts[i] if i < len(type_parts) else type_parts[-1],
                        source_value=value_parts[i] if i < len(value_parts) else value_parts[-1],
                    )

                    sub_rules.append(sub_rule)

                all_items.extend(
                    self.process(sub_rules, raw_json)
                )

            return all_items

        path_expand_rule = self._find_rule(rules, "path_expand")
        list_expand_rule = self._find_rule(rules, "list_expand")
        expand_rule = self._find_rule(rules, "expand")
        explode_rule = self._find_rule(rules, "explode")

        if path_expand_rule:
            return self._process_path_expand_group(
                rules,
                raw_json,
                path_expand_rule
            )

        if list_expand_rule:
            return self._process_expand_group(
                rules,
                raw_json,
                list_expand_rule
            )

        if expand_rule:
            return self._process_expand_group(
                rules,
                raw_json,
                expand_rule
            )

        if explode_rule:
            return self._process_expand_group(
                rules,
                raw_json,
                explode_rule
            )

        obj = self._build_single_object(rules, raw_json)

        # نکته مهم:
        # در groupهای ساده، اگر فقط constantها مقدار داشته باشند
        # مثلا comments[].type = other ولی comments[].text خالی باشد،
        # نباید آیتم خالی داخل آرایه ساخته شود.
        if has_real_source_data(rules, raw_json):
            return [obj]

        return []

   
   
   
   
    def _find_rule(
        self,
        rules: List[Rule],
        source_type: str
    ) -> Optional[Rule]:

        return next(
            (
                r for r in rules
                if (r.source_type or "").lower() == source_type
            ),
            None
        )

    def _default_group_object(self, rules: List[Rule]) -> dict:
        obj = {}

        for rule in rules:
            leaf = rule.target_path.split("[]")[-1].lstrip(".")

            if leaf:
                obj[leaf] = default_value(rule.target_type)

        return obj

    def _build_single_object(
        self,
        rules: List[Rule],
        raw_json: dict
    ) -> dict:

        obj = self._default_group_object(rules)

        for rule in rules:
            handler = HANDLERS.get((rule.source_type or "").lower())

            if not handler:
                continue

            leaf = rule.target_path.split("[]")[-1].lstrip(".")

            if not leaf:
                continue

            value = handler.handle(rule, raw_json)

            if value is not None:
                obj[leaf] = normalize_scalar(
                    value,
                    obj[leaf]
                )

        return obj

    def _process_path_expand_group(
        self,
        rules: List[Rule],
        raw_json: dict,
        expand_rule: Rule
    ) -> List[dict]:

        anchor = expand_rule.source_value

        items = HANDLERS["path_expand"].handle(
            expand_rule,
            raw_json
        )

        if not items:
            return []

        results = []

        for item in items:
            obj = self._default_group_object(rules)

            for rule in rules:
                st = (rule.source_type or "").lower()

                if st == "path_expand":
                    continue

                handler = HANDLERS.get(st)

                if not handler:
                    continue

                leaf = rule.target_path.split("[]")[-1].lstrip(".")

                if not leaf:
                    continue

                value = handler.handle(
                    rule,
                    raw_json,
                    item=item,
                    anchor=anchor
                )

                if value is not None:
                    obj[leaf] = normalize_scalar(
                        value,
                        obj[leaf]
                    )

            if has_meaningful_data(obj):
                results.append(obj)

        return results

    def _process_expand_group(
        self,
        rules: List[Rule],
        raw_json: dict,
        expand_rule: Rule
    ) -> List[dict]:

        handler = HANDLERS.get((expand_rule.source_type or "").lower())

        expand_values = handler.handle(
            expand_rule,
            raw_json
        )

        if not expand_values:
            return []

        results = []

        for i, expand_value in enumerate(expand_values):

            obj = self._default_group_object(rules)

            for rule in rules:
                st = (rule.source_type or "").lower()

                leaf = rule.target_path.split("[]")[-1].lstrip(".")

                if not leaf:
                    continue

                if st in ["expand", "explode", "list_expand"]:
                    obj[leaf] = expand_value

                elif st == "parallel_path":

                    values = HANDLERS["parallel_path"].handle(
                        rule,
                        raw_json
                    )

                    if i < len(values) and values[i] is not None:
                        obj[leaf] = normalize_scalar(
                            values[i],
                            obj[leaf],
                        )

                else:
                    handler = HANDLERS.get(st)

                    if not handler or st == "path_expand":
                        continue

                    value = handler.handle(rule, raw_json)

                    if value is not None:
                        obj[leaf] = normalize_scalar(
                            value,
                            obj[leaf]
                        )

            if has_meaningful_data(obj):
                results.append(obj)

        return results


# =========================================================
# ENGINE
# =========================================================

class MappingEngine:

    def __init__(self, rules: List[Rule]):
        self.rules = rules
        self.schema_builder = SchemaBuilder()
        self.group_processor = GroupProcessor()

    def map_record(self, raw_json: dict) -> dict:

        entity_type = detect_entity_type(raw_json)

        rules = [
            r for r in self.rules
            if r.entity_type == entity_type
        ]

        output = self.schema_builder.build(rules)

        grouped = defaultdict(list)
        scalar_rules = []

        for rule in rules:
            if rule.group:
                grouped[rule.group].append(rule)
            else:
                scalar_rules.append(rule)

        for rule in scalar_rules:
            handler = HANDLERS.get((rule.source_type or "").lower())

            if not handler:
                continue

            value = handler.handle(rule, raw_json)

            if value is not None:
                JsonPath.set(
                    output,
                    rule.target_path,
                    coerce_to_field_type(value, rule.target_type),
                )

        for _, group_rules in grouped.items():
            items = self.group_processor.process(
                group_rules,
                raw_json
            )

            root = group_rules[0].target_path.split("[]")[0]

            JsonPath.set(output, root, items)

        return scrub_na_placeholders(output)


# =========================================================
# LOADER
# =========================================================

def load_rules(
    mapping_file: str,
    source_name: str
) -> List[Rule]:

    df = pd.read_excel(mapping_file)

    type_col = f"{source_name} TYPE"
    value_col = source_name

    rules = []

    for _, row in df.iterrows():

        rule = Rule(
            entity_type=""
            if pd.isna(row.get("Entity Category"))
            else str(row.get("Entity Category")).strip(),

            target_path=""
            if pd.isna(row.get("Field"))
            else str(row.get("Field")).strip(),

            target_type=""
            if pd.isna(row.get("Field Type"))
            else str(row.get("Field Type")).strip(),

            group=None
            if pd.isna(row.get("Group"))
            else str(row.get("Group")).strip(),

            source_type=None
            if pd.isna(row.get(type_col))
            else str(row.get(type_col)).strip().lower(),

            source_value=None
            if pd.isna(row.get(value_col))
            else str(row.get(value_col)).strip(),
        )

        if rule.entity_type and rule.target_path:
            rules.append(rule)

    return rules


# =========================================================
# RUNNER
# =========================================================

def run_mapping(
    input_file: str,
    mapping_file: str,
    output_file: str,
    source_name: str
):
    rules = load_rules(mapping_file, source_name)

    engine = MappingEngine(rules)

    with open(input_file, "r", encoding="utf-8") as fin, \
         open(output_file, "w", encoding="utf-8") as fout:

        for line in fin:
            line = line.strip()

            if not line:
                continue

            raw = json.loads(line)
            mapped = engine.map_record(raw)

            fout.write(
                json.dumps(mapped, ensure_ascii=False) + "\n"
            )

    return output_file
