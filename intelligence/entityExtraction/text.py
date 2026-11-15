"""Prepare news and preserve exact entity positions and evidence."""

from __future__ import annotations

import math
import re

from .contract import ENTITY_TYPES, EntityExtractionError


def prepare_news(
    title: str | None,
    body: str | None,
) -> dict:
    """Prepare the article without changing its original text."""

    for key, value in (("title", title), ("body", body)):
        if value is not None and not isinstance(value, str):
            raise TypeError(f"{key} must be a string or None.")

    title = title or ""
    body = body or ""

    return {
        "sample_id": "news",
        "title": title,
        "body": body,
        "text": title + "\n\n" + body,
    }


def chunks(text, tokenizer, size=320, overlap=64):
    """Split long text into overlapping token windows."""

    encoded = tokenizer(
        text,
        add_special_tokens=False,
        return_offsets_mapping=True,
        truncation=False,
        verbose=False,
    )

    offsets = encoded["offset_mapping"]

    if not offsets:
        return

    index = 0

    while index < len(offsets):
        end_index = min(index + size, len(offsets))

        start = offsets[index][0]
        end = offsets[end_index - 1][1]

        # Expand to word boundaries where possible.
        while start > 0 and not text[start - 1].isspace():
            start -= 1

        while end < len(text) and not text[end].isspace():
            end += 1

        while end_index > index + 1:
            window_tokens = tokenizer(
                text[start:end],
                add_special_tokens=False,
                truncation=False,
                verbose=False,
            )["input_ids"]

            if len(window_tokens) <= size + 48:
                break

            end_index -= 1
            end = offsets[end_index - 1][1]

        if end > start:
            yield start, end, text[start:end]

        if end_index == len(offsets):
            break

        index = max(index + 1, end_index - overlap)


def context(text, start, end):
    """Read the evidence sentence/segment directly from the source."""

    boundaries = [0]

    abbreviations = {
        "mr", "mrs", "ms", "dr", "atty", "prof",
        "no", "nos", "vs", "jr", "sr",
        "inc", "corp", "co", "ltd",
        "gen", "lt", "col", "capt", "rep",
        "st", "sta", "brgy",
        "jan", "feb", "mar", "apr", "jun", "jul",
        "aug", "sep", "sept", "oct", "nov", "dec",
    }

    pattern = r"[.!?]+[”’\"')\]]*(?=\s|$)|\n\s*\n"

    for match in re.finditer(pattern, text):
        # A boundary inside the entity itself is ignored.
        if start < match.end() and match.start() < end:
            continue

        if text[match.start()] == ".":
            word_match = re.search(
                r"([\w.]+)$",
                text[:match.start()],
            )

            word = word_match.group(1) if word_match else ""

            is_abbreviation = word.lower() in abbreviations
            is_initial = re.fullmatch(
                r"(?:[A-Za-z]\.)*[A-Za-z]",
                word,
            )

            if is_abbreviation or is_initial:
                continue

        boundaries.append(match.end())

    boundaries.append(len(text))

    left = max(
        value for value in boundaries
        if value <= start
    )

    right = min(
        value for value in boundaries
        if value >= end
    )

    return text[left:right].strip()


def validate_mention(row, mention):
    """Check that a mention and its evidence exist in the source."""

    start = mention["start"]
    end = mention["end"]
    field = mention["field"]

    valid_span = (
        mention["entity_type"] in ENTITY_TYPES
        and field in {"title", "body"}
        and isinstance(start, int)
        and isinstance(end, int)
        and 0 <= start < end <= len(row["text"])
        and row["text"][start:end] == mention["name"]
    )

    if not valid_span:
        raise EntityExtractionError(
            "An entity does not match its exact input span."
        )

    first = mention["field_start"]
    last = mention["field_end"]
    source = row[field]

    valid_field_span = (
        0 <= first < last <= len(source)
        and source[first:last] == mention["name"]
    )

    if not valid_field_span:
        raise EntityExtractionError(
            "Entity field offsets do not match the source."
        )

    quote = mention["evidence_sentence"]

    if (
        not quote
        or quote not in source
        or mention["name"] not in quote
    ):
        raise EntityExtractionError(
            "Entity evidence does not match the source."
        )


def collect_mentions(
    row,
    adapter,
    *,
    chunk_tokens=320,
    chunk_overlap=64,
):
    """Collect one model's mentions and remove window duplicates."""

    found = {}
    title_end = len(row["title"])

    windows = chunks(
        row["text"],
        adapter.tokenizer,
        size=chunk_tokens,
        overlap=chunk_overlap,
    )

    for offset, window_end, window in windows:
        for candidate in adapter.extract(window):
            start = offset + int(candidate["start"])
            end = offset + int(candidate["end"])

            if not offset <= start < end <= window_end:
                continue

            # Remove only surrounding whitespace from a prediction.
            while start < end and row["text"][start].isspace():
                start += 1

            while end > start and row["text"][end - 1].isspace():
                end -= 1

            # Reject empty spans and spans crossing title/body.
            if (
                start == end
                or start < title_end < end
                or start < title_end + 2 < end
            ):
                continue

            field = "title" if end <= title_end else "body"
            base = 0 if field == "title" else title_end + 2

            score = float(candidate["score"])

            if not math.isfinite(score):
                raise EntityExtractionError(
                    "The model returned a non-finite score."
                )

            item = {
                "name": row["text"][start:end],
                "entity_type": candidate["type"],
                "start": start,
                "end": end,
                "field": field,
                "field_start": start - base,
                "field_end": end - base,
                "score": score,
                "evidence_sentence": context(
                    row[field],
                    start - base,
                    end - base,
                ),
            }

            validate_mention(row, item)

            key = (
                start,
                end,
                item["entity_type"],
            )

            # Repeated predictions from the same model count once.
            if key not in found or score > found[key]["score"]:
                found[key] = item

    return sorted(
        found.values(),
        key=lambda item: (
            item["start"],
            item["end"],
            item["entity_type"],
        ),
    )