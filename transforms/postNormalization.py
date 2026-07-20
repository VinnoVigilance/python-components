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

    date_order = (config or {}).get("date_order", "DMY")

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

    def __init__(self, rules_df: pd.DataFrame, config=None):
        self.rules_df = rules_df.sort_values("priority")

        # Carries per source settings such as date_order, so a handler
        # can read a date the way the list that published it writes them
        self.config = config or {}

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


def post_normalize_record(record, rules_df):
    engine = PostNormalizationEngine(rules_df)
    return engine.post_normalize_record(record)


def run_post_normalization(jsonl_data, rules_df):
    engine = PostNormalizationEngine(rules_df)
    return engine.run_post_normalization(jsonl_data)