"""Shared text and layout operations for the standalone DBM PDF readers."""

import re
from bisect import bisect_right

from pdfplumber.utils import cluster_objects


PAGINATION = re.compile(r"(?:\d+|PB)(?:\s+(?:\d+|PB))*", re.IGNORECASE)


def line_text(words):
    return " ".join(word["text"] for word in words)


def bold(word):
    return "bold" in word["fontname"].lower()


def split_row_at_columns(words, boundaries):
    """Assign words on a boundary to the column on its right."""
    cells = [[] for _ in range(len(boundaries) + 1)]
    for word in words:
        cells[bisect_right(boundaries, word["x0"])].append(word)
    return cells


def source_line(words, page):
    return {
        "source_page": page, "top": round(words[0]["top"], 2), "x": round(words[0]["x0"], 2),
        "text": line_text(words),
    }


def positioned_lines(page):
    lines = []
    words = page.extract_words(extra_attrs=["fontname"])
    for row in cluster_objects(words, "top", 2):
        line = sorted(
            (word for word in row if 0 <= word["x0"] < page.width and word["top"] < page.height * .95),
            key=lambda word: word["x0"],
        )
        if line:
            lines.append(line)
    return lines


def positioned_range(pdf, first, last):
    for number in range(first, last + 1):
        for words in positioned_lines(pdf.pages[number - 1]):
            if not PAGINATION.fullmatch(line_text(words)):
                yield number, words
