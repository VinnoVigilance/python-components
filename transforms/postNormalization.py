import pandas as pd
import json
from copy import deepcopy
from datetime import datetime
import re

from transforms.dateResolver import resolve_dates


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


HANDLERS = {
    "EMPTY_DEPENDENCY": empty_dependency_handler,
    "DATE_NORMALIZATION": date_normalization_handler,
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