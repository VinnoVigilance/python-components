import pandas as pd
import json
from copy import deepcopy
from datetime import datetime
import re


def empty_dependency_handler(entity, rule):
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


def date_normalization_handler(entity, rule):
    source_path = rule["condition_path"]

    if source_path not in entity:
        return

    values = entity.get(source_path)

    if not isinstance(values, list):
        return

    date_patterns = [
        r"\b\d{4}-\d{2}-\d{2}(?:\s\d{2}:\d{2}:\d{2})?\b",
        r"\b\d{1,2}\s+[A-Za-z]+\s+\d{4}\b",
        r"\b[A-Za-z]+\s+\d{1,2},\s*\d{4}\b",
        r"\b\d{1,2}/\d{1,2}/\d{4}\b",
        r"\b\d{1,2}/\d{4}\b",
        r"\bApproximately\s+\d{4}\b",
        r"\b\d{4}\s*\(\s*\d{4}\s*\)\b",
        r"\b\d{4}\b",
    ]

    normalized = []
    seen = set()

    for item in values:
        item_type = item.get("Type") or item.get("type") or ""
        raw_text = (
            item.get("Note")
            or item.get("note")
            or item.get("date_full")
            or item.get("Year")
            or item.get("year")
            or ""
        )

        raw_text = re.sub(r"\s+", " ", str(raw_text).strip())

        if not raw_text:
            continue

        parts = [
            p.strip()
            for p in re.split(r"\s*[,;]\s*", raw_text)
            if p.strip()
        ]

        for part in parts:
            if re.fullmatch(
                r"between\s+\d{4}\s+and\s+\d{4}",
                part,
                flags=re.IGNORECASE,
            ):
                key = ("", "", "", part)

                if key not in seen:
                    seen.add(key)
                    normalized.append({
                        "Day": "",
                        "Month": "",
                        "Year": "",
                        "Type": item_type,
                        "IsApproximate": "",
                        "Note": part,
                    })

                continue

            matches = []
            consumed_spans = []

            for pattern in date_patterns:
                for match in re.finditer(pattern, part, flags=re.IGNORECASE):
                    start, end = match.span()

                    if any(start < e and end > s for s, e in consumed_spans):
                        continue

                    consumed_spans.append((start, end))
                    matches.append((start, match.group()))

            matches.sort(key=lambda x: x[0])

            for _, token in matches:
                token = token.strip()
                parsed_dates = []

                try:
                    dt = datetime.strptime(token[:10], "%Y-%m-%d")
                    parsed_dates.append(
                        (str(dt.year), f"{dt.month:02}", f"{dt.day:02}")
                    )
                except:
                    pass

                if not parsed_dates:
                    for fmt in [
                        "%d %B %Y",
                        "%d %b %Y",
                        "%B %d, %Y",
                        "%b %d, %Y",
                    ]:
                        try:
                            dt = datetime.strptime(token, fmt)
                            parsed_dates.append(
                                (str(dt.year), f"{dt.month:02}", f"{dt.day:02}")
                            )
                            break
                        except:
                            pass

                if (
                    not parsed_dates
                    and re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", token)
                ):
                    day, month, year = token.split("/")
                    parsed_dates.append(
                        (year, month.zfill(2), day.zfill(2))
                    )

                if (
                    not parsed_dates
                    and re.fullmatch(r"\d{1,2}/\d{4}", token)
                ):
                    month, year = token.split("/")
                    parsed_dates.append(
                        (year, month.zfill(2), "")
                    )

                if (
                    not parsed_dates
                    and re.fullmatch(r"\d{4}\s*\(\s*\d{4}\s*\)", token)
                ):
                    for year in re.findall(r"\d{4}", token):
                        parsed_dates.append((year, "", ""))

                if not parsed_dates:
                    year_match = re.search(r"\d{4}", token)

                    if year_match:
                        parsed_dates.append(
                            (year_match.group(), "", "")
                        )

                for year, month, day in parsed_dates:
                    key = (year, month, day, part)

                    if key in seen:
                        continue

                    seen.add(key)

                    normalized.append({
                        "Day": day,
                        "Month": month,
                        "Year": year,
                        "Type": item_type,
                        "IsApproximate": (
                            "true"
                            if "approx" in part.lower()
                            else "false"
                        ),
                        "Note": part,
                    })

    entity[source_path] = normalized


def deduplicate_all_arrays_handler(entity, rule):

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

    def __init__(self, rules_df: pd.DataFrame):
        self.rules_df = rules_df.sort_values("priority")

    def post_normalize_record(self, record):
        entity = deepcopy(record)

        for _, rule in self.rules_df.iterrows():
            rule_type = rule["rule_type"]
            handler = HANDLERS.get(rule_type)

            if handler:
                handler(entity, rule)

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