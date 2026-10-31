"""Research-only contracts. No production imports, models or network access."""

import hashlib
import json
import re
import unicodedata
from typing import Any

PREPARATION_VERSION = "adverse-preparation-v1"
POLICY_VERSION = "adverse-news-policy-v0.2"
SPLITS = {"pilot", "train", "dev", "final", "challenge", "excluded"}


def digest(value: Any) -> str:
    """SHA-256 of canonical JSON; also used for deterministic identifiers."""
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def file_digest(path) -> str:
    result = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(chunk)
    return result.hexdigest()


def text(value: Any, name: str, optional: bool = False) -> str:
    if optional and value is None:
        return ""
    if not isinstance(value, str) or (not optional and not value.strip()):
        raise ValueError(f"{name} must be {'a string or null' if optional else 'a nonempty string'}")
    return value


def normalized_body(body: str) -> str:
    """Whitespace/Unicode normalization only; retain case and punctuation."""
    return " ".join(unicodedata.normalize("NFC", body).split())


def adapt(row: dict, adapter: str, expected_dataset: str | None = None) -> dict:
    """Allowlist text/identity fields; taxonomy/categories never enter features."""
    if not isinstance(row, dict):
        raise ValueError("article row must be a JSON object")
    if adapter == "canonical":
        content = row.get("Content") or {}
        source = row.get("Sources") or {}
        taxonomy = row.get("Taxonomy") or {}
        dates = row.get("Dates") or {}
        fields = {
            "source_name": source.get("SourceName"),
            "dataset_name": source.get("DatasetName"),
            "source_record_id": source.get("SourceRecordId"),
            "url": source.get("SourceURL"),
            "title": content.get("Title"),
            "body": content.get("BodyText"),
            "published_at": dates.get("FullDate"),
            "published_original": dates.get("OriginalValue"),
            "ingested_at": None,
            "language": taxonomy.get("Language"),
            "source_version": None,
        }
    elif adapter == "database":
        # Flat JSONL contract; deliberately no guessing from ambiguous aliases.
        fields = {key: row.get(key) for key in (
            "source_name", "dataset_name", "source_record_id", "url", "title", "body",
            "published_at", "published_original", "ingested_at", "language", "source_version",
        )}
    else:
        raise ValueError(f"unknown adapter: {adapter}")

    for key in ("source_name", "dataset_name"):
        fields[key] = text(fields[key], key)
    if expected_dataset and fields["dataset_name"] != expected_dataset:
        raise ValueError("dataset_name differs from configured filename")
    for key in ("source_record_id", "source_version"):
        value = fields[key]
        if type(value) is int:
            fields[key] = str(value)
        else:
            fields[key] = text(value, key, optional=True)
    for key in ("url", "title", "published_at", "published_original", "ingested_at", "language"):
        fields[key] = text(fields[key], key, optional=True)
    identity = fields["source_record_id"] or fields["url"]
    if not identity:
        raise ValueError("source_record_id or url required for logical identity")
    # Missing/invalid body is an eligibility failure, not an adapter exception.
    logical_id = digest([fields["source_name"], fields["dataset_name"], identity])
    content_hash = digest([fields["title"], fields["body"]])
    fields.update(
        logical_id=logical_id,
        article_id=digest([logical_id, fields["source_version"], content_hash]),
        content_hash=content_hash,
        body_group_hash=(digest(normalized_body(fields["body"]))
                         if isinstance(fields["body"], str) and fields["body"].strip() else None),
        preparation_version=PREPARATION_VERSION,
    )
    return fields


# Only whole-body placeholders. A real article quoting these words is retained.
PLACEHOLDERS = {
    "access denied", "403 forbidden", "404 not found", "page not found",
    "content unavailable", "article unavailable", "internal server error",
    "service unavailable", "enable javascript", "please enable javascript",
    "please accept cookies", "extraction error",
}


def eligibility(article: dict, supported_languages: list[str]) -> tuple[list[str], list[str]]:
    body = article["body"]
    reasons, warnings = [], []
    if body is None:
        reasons.append("missing_body")
    elif not isinstance(body, str):
        reasons.append("invalid_body_type")
    elif not body.strip():
        reasons.append("empty_body")
    elif normalized_body(body).casefold().strip(" .!\n\t") in PLACEHOLDERS:
        reasons.append("placeholder_only")
    else:
        if "\ufffd" in body or "\x00" in body:
            warnings.append("inspect_possible_corruption")
        if re.search(r"<\s*(?:html|body|script)\b", body, re.I):
            warnings.append("inspect_possible_html_extraction")
    language = article["language"].strip().casefold()
    if not language:
        warnings.append("language_unverified")
    elif supported_languages and language not in {x.casefold() for x in supported_languages}:
        reasons.append("language_requires_other_workflow")
    if not article["title"].strip():
        warnings.append("missing_title_allowed")
    if not article["published_at"]:
        warnings.append("publication_date_missing")
    return reasons, warnings


class Groups:
    """Connected components: aliases, duplicate bodies and reviewed case links."""

    def __init__(self):
        self.parents = {}

    def find(self, item):
        self.parents.setdefault(item, item)
        root = item
        while self.parents[root] != root:
            root = self.parents[root]
        while self.parents[item] != item:
            next_item = self.parents[item]
            self.parents[item] = root
            item = next_item
        return root

    def join(self, a, b):
        a, b = self.find(a), self.find(b)
        self.parents[max(a, b)] = min(a, b)


def model_input(article: dict) -> dict:
    """The sole semantic input boundary, preserving full text."""
    return {"title": article["title"], "body": article["body"]}


def teacher_label(response: str) -> int:
    """Strict transport parser. Caller logs failures separately without a label."""
    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate response key")
            result[key] = value
        return result

    output = json.loads(response, object_pairs_hook=unique_keys)
    if not isinstance(output, dict) or set(output) != {"label"}:
        raise ValueError("teacher response must contain exactly label")
    if type(output["label"]) is not int or output["label"] not in (0, 1):
        raise ValueError("teacher label must be integer 0/1")
    return output["label"]
