"""Extract the Senate roster, leadership, committees and secretariat from the DBM directory."""

import re
from collections import Counter
from contextlib import closing

import pdfplumber
import pypdfium2

from .layout import bold, positioned_range, source_line
from .representatives import split_columns
from .house_officials import HOUSE_ROOT
from .common import VACANCIES, contact
from .house import HOUSE


ROSTER = "SENATE OF THE PHILIPPINES"
COMMITTEES = "SENATE PERMANENT COMMITTEES"
SECRETARIAT = "SENATE SECRETARIAT OFFICIALS"
HEADINGS = (ROSTER, COMMITTEES, SECRETARIAT)
TITLE = re.compile(
    r"(?:(?:Senate |Deputy )?(?:Majority|Minority) (?:Floor )?Leader|"
    r"Senate President(?: Pro[ -]Tempore)?|Senate Secretary|Senate Legal Counsel|"
    r"Sergeant-at-Arms|Deputy Secretary for .+|Director General)$",
    re.IGNORECASE,
)
OFFICE = re.compile(r"\bo\s*ffi\s*ce\b", re.IGNORECASE)
CONTINUED_LABEL = re.compile(r"^(?:and|of|for|or)\b")


def find_pages(pdf_path):
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        first = None
        for index in range(len(pdf)):
            with closing(pdf[index]) as page, closing(page.get_textpage()) as text_page:
                lines = {" ".join(line.upper().split()) for line in text_page.get_text_range().splitlines()}
            if ROSTER in lines:
                first = index + 1
            if first is not None and HOUSE in lines:
                return first, index + 1
    raise ValueError("Could not locate the Senate and following House headings")


def is_contact(text):
    return contact(text) or bool(re.match(r"loc\s*\.", text, re.IGNORECASE))


def new_listing(name, office=None):
    return {
        "printed_name": name["text"] if name else None,
        "name_source": name,
        "vacant": bool(name and name["text"].upper() in VACANCIES),
        "role_source": None,
        "office_lines": [office] if office else [],
        "contact_lines": [],
    }


def read_committees(rows, section, issues):
    if not rows:
        issues.append({"text": COMMITTEES, "reason": "No Senate committee rows found"})
        return
    left_edge = Counter(round(words[0]["x0"]) for _, words in rows).most_common(1)[0][0]
    right_edge = Counter(round(words[-1]["x1"]) for _, words in rows).most_common(1)[0][0]
    gaps = [
        words[0]["top"] - previous[0]["top"]
        for (previous_page, previous), (page, words) in zip(rows, rows[1:])
        if previous_page == page
    ]
    # Grouped layouts have adjacent lines at one baseline step and group
    # breaks at two. Later layouts have a single spacing and repeat each chair.
    close_gap = (min(gaps) + max(gaps)) / 2 if gaps and max(gaps) >= 1.6 * min(gaps) else 0
    chair = previous_line = None
    for page, words in rows:
        src = source_line(words, page)
        left, right = split_columns(words, left_edge, right_edge)
        adjacent = (
            previous_line is not None
            and previous_line["source_page"] == page
            and src["top"] - previous_line["top"] < close_gap
        )
        previous_line = src
        if not left:
            issues.append({**src, "reason": "Senate committee row has no label"})
            chair = None
            continue
        label = source_line(left, page)
        if not right and CONTINUED_LABEL.match(label["text"]):
            if adjacent and section["records"]:
                section["records"][-1]["committee_lines"].append(label)
            else:
                issues.append({**label, "reason": "Unattached Senate committee label continuation"})
            continue
        if right:
            chair = source_line(right, page)
            assignment = "printed"
        elif adjacent and chair is not None:
            assignment = "shared"
        else:
            chair = None
            assignment = None
        section["records"].append({
            "committee_lines": [label],
            "chair_source": chair,
            "chair_assignment": assignment,
        })
    if not any(row["chair_assignment"] == "printed" for row in section["records"]):
        issues.append({"text": COMMITTEES, "reason": "Could not locate the Senate chairperson column"})


def read(pdf_path):
    first, last = find_pages(pdf_path)
    roster = {"heading": None, "header_lines": [], "records": []}
    secretariat = {"heading": None, "header_lines": [], "records": []}
    committees = {"heading": None, "header_lines": [], "records": []}
    result = {
        "pdf": str(pdf_path), "roster": roster, "committees": committees,
        "secretariat": secretariat, "issues": [],
    }
    committee_rows = []
    section = None
    record = None
    seen = []
    with pdfplumber.open(pdf_path) as pdf:
        for page, words in positioned_range(pdf, first, last):
            src = source_line(words, page)
            value = src["text"]
            heading = value.upper()
            if heading in {HOUSE_ROOT, HOUSE}:
                break
            if heading in HEADINGS:
                seen.append(heading)
                section = {ROSTER: roster, COMMITTEES: committees, SECRETARIAT: secretariat}[heading]
                record = None
                if section is not None:
                    section["heading"] = src
                continue
            if section is None:
                continue
            if section is committees:
                if value.casefold() == "chairpersons":
                    section["header_lines"].append(src)
                else:
                    committee_rows.append((page, words))
                continue

            name_end = next((i for i, word in enumerate(words) if not bold(word)), len(words))
            # The Senate header ends at the first name with an inline contact.
            # Its address, congress number and main phone can also use bold text.
            tail = source_line(words[name_end:], page) if 0 < name_end < len(words) else None
            contact_start = next((i for i in range(len(words)) if is_contact(words[i]["text"])), len(words))
            title = source_line(words[:contact_start], page) if contact_start else None
            if record is not None and title and TITLE.fullmatch(title["text"]):
                if record["role_source"]:
                    result["issues"].append({**src, "reason": "Second title for Senate listing"})
                    continue
                record["role_source"] = title
                if contact_start < len(words):
                    record["contact_lines"].append(source_line(words[contact_start:], page))
            elif section is roster and record is None and not (tail and is_contact(tail["text"])):
                section["header_lines"].append(src)
            elif is_contact(value):
                target = record["contact_lines"] if record else section["header_lines"]
                target.append(src)
            elif name_end:
                record = new_listing(source_line(words[:name_end], page))
                section["records"].append(record)
                if tail:
                    if is_contact(tail["text"]):
                        record["contact_lines"].append(tail)
                    elif section is secretariat and OFFICE.search(tail["text"]):
                        record["office_lines"].append(tail)
                    else:
                        result["issues"].append({**tail, "reason": "Unrecognized detail beside Senate name"})
            elif section is secretariat and OFFICE.search(value):
                record = new_listing(None, office=src)
                section["records"].append(record)
            else:
                result["issues"].append({**src, "reason": "Unrecognized Senate line"})

    if seen != list(HEADINGS):
        result["issues"].append({"observed": seen, "reason": "Unexpected Senate section boundaries"})
    read_committees(committee_rows, committees, result["issues"])
    for section in (roster, secretariat):
        if not section["records"]:
            result["issues"].append({"heading": section["heading"], "reason": "No Senate listings found"})
    for record in secretariat["records"]:
        if record["name_source"] and not record["role_source"]:
            result["issues"].append({**record["name_source"], "reason": "Missing Senate secretariat title"})
    return result
