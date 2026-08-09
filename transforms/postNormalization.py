import pandas as pd
import json
import html
from copy import deepcopy
from datetime import datetime
import re

from transforms.dateResolver import resolve_dates
from transforms.searchEnrichment import (
    normalize_text,
    tokenize,
    phonetic_key,
    detect_script,
    canonicalize_script,
    country_to_iso2,
    normalize_number,
)


def empty_dependency_handler(entity, rule, config=None):
    condition_path = rule["condition_path"]
    target_path = rule["target_path"]
    value = rule["value"]

    src_list_name = condition_path.split("[]")[0].strip(".")
    tgt_list_name = target_path.split("[]")[0].strip(".")

    src_key = condition_path.split("[]")[1].strip(".")
    tgt_key = target_path.split("[]")[1].strip(".")

    if src_list_name not in entity or tgt_list_name not in entity:
        return

    src_list = entity[src_list_name]
    tgt_list = entity[tgt_list_name]

    max_len = max(len(src_list), len(tgt_list))

    while len(src_list) < max_len:
        src_list.append({})

    while len(tgt_list) < max_len:
        tgt_list.append({})

    for i in range(max_len):
        if src_list[i].get(src_key) in [None, ""]:
            tgt_list[i][tgt_key] = value

    entity[src_list_name] = src_list
    entity[tgt_list_name] = tgt_list


def date_normalization_handler(entity, rule, config=None):
    source_path = rule["condition_path"]

    if source_path not in entity:
        return

    values = entity.get(source_path)

    if not isinstance(values, list):
        return

    # If a DATE_NORMALIZATION rule is running then date_order genuinely
    # decides how ambiguous dates read (03/04 as 3 Apr under DMY vs 4 Mar
    # under MDY), so a missing order is an error, not a thing to guess.
    if not config or "date_order" not in config:
        raise ValueError(
            "DATE_NORMALIZATION rule requires 'date_order' in config "
            "(e.g. 'DMY' or 'MDY'); none was provided."
        )

    date_order = config["date_order"]

    entity[source_path] = resolve_dates(values, date_order)


def deduplicate_all_arrays_handler(entity, rule, config=None):

    def make_hashable(value):
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (k, make_hashable(v))
                    for k, v in value.items()
                )
            )

        if isinstance(value, list):
            return tuple(make_hashable(v) for v in value)

        return value

    def deduplicate(value):
        if isinstance(value, dict):
            return {
                k: deduplicate(v)
                for k, v in value.items()
            }

        if isinstance(value, list):
            result = []
            seen = set()

            for item in value:
                cleaned_item = deduplicate(item)
                item_key = make_hashable(cleaned_item)

                if item_key in seen:
                    continue

                seen.add(item_key)
                result.append(cleaned_item)

            return result

        return value

    cleaned = deduplicate(deepcopy(entity))

    entity.clear()
    entity.update(cleaned)


# The search-support transforms a SEARCH_ENRICH rule can apply. Keyed by the
# rule's ``action`` column so one handler covers Normalized_*, Search_Tokens and
# Phonetic_Key without a handler each.
_SEARCH_TRANSFORMS = {
    "NORMALIZE": normalize_text,
    "TOKENIZE": tokenize,
    "PHONETIC": phonetic_key,
    "SCRIPT": detect_script,
    "CANONICAL_SCRIPT": canonicalize_script,
    "COUNTRY_CODE": country_to_iso2,
    "NORMALIZE_NUMBER": normalize_number,
}


def _split_array_path(path):
    """Split "Names[].Name" into ("Names", "Name").

    Both the source (``condition_path``) and target (``target_path``) of a
    SEARCH_ENRICH rule name a leaf inside the same array, e.g. the name lives at
    ``Names[].Name`` and its normalized form at ``Names[].Normalized_Name``.
    """
    left, _, right = str(path).partition("[]")
    return left.strip(". "), right.strip(". ")


def search_enrich_handler(entity, rule, config=None):
    """Fill a computed search field on every element of an array.

    Reads the source leaf named by ``condition_path`` (e.g. Names[].Name) and
    writes ``target_path``'s leaf (e.g. Names[].Normalized_Name) with the
    transform named in ``action``. When ``condition`` is "IF_EMPTY" an element
    that already carries a value (e.g. a Language the source provided) is left
    untouched; otherwise the computed value always wins, since these fields have
    no meaning except as a fresh derivation of the source text.
    """
    source_list, source_leaf = _split_array_path(rule["condition_path"])
    target_list, target_leaf = _split_array_path(rule["target_path"])

    action = str(rule.get("action") or "").strip().upper()
    transform = _SEARCH_TRANSFORMS.get(action)

    if transform is None:
        return

    only_if_empty = (
        str(rule.get("condition") or "").strip().upper() == "IF_EMPTY"
    )

    items = entity.get(source_list)

    if not isinstance(items, list):
        return

    for item in items:
        if not isinstance(item, dict):
            continue

        if only_if_empty and item.get(target_leaf) not in (None, "", [], {}):
            continue

        item[target_leaf] = transform(item.get(source_leaf))


def enum_normalize_handler(entity, rule, config=None):
    """Rewrite a canonical enum leaf using a ``word=value`` table.

    For every element of the array named by ``condition_path`` (e.g.
    Dates[].IsApproximate), map the leaf's raw word to the value listed in the
    rule's ``value`` cell (``exact=false|approximately=true|...``) and write it
    to ``target_path``. Matching is case-insensitive, so "EXACT" reads like
    "exact"; a word the table does not list is left unchanged.
    """
    source_list, source_leaf = _split_array_path(rule["condition_path"])

    target_path = str(rule.get("target_path") or "").strip()
    target_leaf = (
        _split_array_path(target_path)[1] if target_path else source_leaf
    )

    mapping = {}
    for pair in str(rule.get("value") or "").split("|"):
        if "=" not in pair:
            continue
        word, result = pair.split("=", 1)
        mapping[word.strip().lower()] = result.strip()

    items = entity.get(source_list)
    if not isinstance(items, list):
        return

    for item in items:
        if not isinstance(item, dict):
            continue

        raw = item.get(source_leaf)
        key = "" if raw is None else str(raw).strip().lower()

        if key in mapping:
            item[target_leaf] = mapping[key]


def _strip_html(value):
    """Remove HTML tags and unescape entities from a single string.

    Order matters: entities are unescaped **first** (``&amp;`` -> ``&``), then
    tags are stripped. Some sources double-encode markup (a literal
    ``&lt;p&gt;`` sitting inside real ``<p>`` tags); unescaping first turns that
    back into a ``<p>`` tag so the strip catches it too -- stripping first would
    leave the resurrected tag behind. Tags become a space (not "") so block
    boundaries like ``</p><p>`` don't glue words together; runs of whitespace
    then collapse to one. Anything that isn't a string passes through untouched.
    """
    if not isinstance(value, str):
        return value

    unescaped = html.unescape(value)
    without_tags = re.sub(r"<[^>]+>", " ", unescaped)
    collapsed = re.sub(r"\s+", " ", without_tags)
    return collapsed.strip()


def sanitize_html_handler(entity, rule, config=None):
    """Strip HTML from a free-text leaf on every element of an array.

    Reads the array leaf named by ``condition_path`` (e.g. Comments[].text) and
    rewrites each element's value with the markup removed. Generic across all
    lists -- any source that leaks HTML into a text field is cleaned here, with
    no per-source branch. Targeted at named free-text paths only, so structured
    fields (identifiers, dates, numbers) are never touched. Runs before
    DEDUPLICATE so two texts that differ only in markup collapse to one.
    """
    source_list, source_leaf = _split_array_path(rule["condition_path"])

    items = entity.get(source_list)
    if not isinstance(items, list):
        return

    for item in items:
        if not isinstance(item, dict):
            continue

        value = item.get(source_leaf)
        if isinstance(value, str):
            item[source_leaf] = _strip_html(value)


HANDLERS = {
    "EMPTY_DEPENDENCY": empty_dependency_handler,
    "DATE_NORMALIZATION": date_normalization_handler,
    "ENUM_NORMALIZE": enum_normalize_handler,
    "SANITIZE_HTML": sanitize_html_handler,
    "SEARCH_ENRICH": search_enrich_handler,
    "DEDUPLICATE_ALL_ARRAYS": deduplicate_all_arrays_handler,
}


class PostNormalizationEngine:

    def __init__(self, rules_df: pd.DataFrame, config: dict):
        self.rules_df = rules_df.sort_values("priority")

        # Carries per source settings such as date_order, so a handler
        # can read a date the way the list that published it writes them.
        # Required rather than optional: a silent default here is what let
        # a caller drop the config and fall back to DMY without noticing.
        self.config = config

    def post_normalize_record(self, record):
        entity = deepcopy(record)

        for _, rule in self.rules_df.iterrows():
            rule_type = rule["rule_type"]
            handler = HANDLERS.get(rule_type)

            if handler:
                handler(entity, rule, self.config)

        return entity

    def run_post_normalization(self, jsonl_data):
        return [
            self.post_normalize_record(record)
            for record in jsonl_data
        ]


def load_jsonl(path):
    data = []

    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            data.append(json.loads(line))

    return data


def save_jsonl(data, path):
    with open(path, "w", encoding="utf-8") as f:
        for item in data:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")


def post_normalize_record(record, rules_df, config):
    engine = PostNormalizationEngine(rules_df, config)
    return engine.post_normalize_record(record)


def run_post_normalization(jsonl_data, rules_df, config):
    engine = PostNormalizationEngine(rules_df, config)
    return engine.run_post_normalization(jsonl_data)