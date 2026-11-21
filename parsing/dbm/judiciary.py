"""Extract the judiciary listings and their court and roster context."""

import re
from itertools import takewhile

import pdfplumber
import pypdfium2

from .layout import bold, line_text, positioned_lines, source_line
from .common import VACANCIES, is_contact
from .local_government import find_table_pages


COURTS = {"SUPREME COURT OF THE PHILIPPINES", "COURT OF APPEALS", "COURT OF TAX APPEALS", "SANDIGANBAYAN"}
ROSTER = re.compile(r"(Associate Justices)(?: \(([^)]+)\))?:?", re.IGNORECASE)
DIVISION = re.compile(r"((?:First|Second|Third|Fourth|Fifth|Sixth|Seventh) Division)\b", re.IGNORECASE)
PAIR = ("Senior", "Member", "Junior", "Member")
CONTACT_LABEL = re.compile(r"^(?:Trunkline|Phone|Tel(?:ephone)?|Fax|Website|E-?mail)\s*:", re.IGNORECASE)
ROLE = re.compile(
    r"(?:Chief Justice|(?:Acting )?Presiding Justice(?: and Chairperson)?|Executive Justice|"
    r"(?:Concurrent )?Chairperson|(?:Senior|Junior)(?: Member)?|Member)", re.IGNORECASE,
)


def find_pages(pdf_path):
    provincial, _ = find_table_pages(pdf_path, "provincial")
    last = None
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for index in range(provincial - 2, -1, -1):
            page = pdf[index]
            text_page = page.get_textpage()
            try:
                text = " ".join(text_page.get_text_range().upper().split())
            finally:
                text_page.close()
                page.close()
            if last is None and "SEVENTH DIVISION" in text and "JUNIOR MEMBER" in text:
                last = index + 1
            if last is not None and "CHIEF JUSTICE" in text and "SUPREME COURT OF THE PHILIPPINES" in text:
                return index + 1, last
    raise ValueError("Could not establish the judiciary body range before the provincial table")


def is_contact_line(text):
    return text in {"-", "–", "—"} or is_contact(text) or CONTACT_LABEL.match(text)


def new_record(words, page, owner):
    name = re.sub(r" +\.(?= |$)", ".", line_text(words))
    record = {
        "printed_name": name, "name_source": source_line(words, page), "vacant": name.upper() in VACANCIES,
        "role_lines": [], "contact_lines": [],
    }
    owner["records"].append(record)
    return record


def split_pair(words, divider):
    return (
        [word for word in words if word["x0"] < divider],
        [word for word in words if word["x0"] >= divider],
    )


def read_member_pair(lines, index, page, group):
    roles = lines[index + 1]
    if tuple(word["text"] for word in roles) != PAIR:
        return 0
    divider = (roles[1]["x1"] + roles[2]["x0"]) / 2
    names = split_pair(lines[index], divider)
    if not all(words and all(map(bold, words)) for words in names):
        return 0
    records = [new_record(words, page, group) for words in names]
    for record, words in zip(records, split_pair(roles, divider)):
        record["role_lines"].append(source_line(words, page))
    if index + 2 < len(lines) and is_contact_line(line_text(lines[index + 2])):
        for record, words in zip(records, split_pair(lines[index + 2], divider)):
            if words:
                record["contact_lines"].append(source_line(words, page))
        return 3
    return 2


def read(pdf_path):
    first, last = find_pages(pdf_path)
    result = {"source": str(pdf_path), "pages": [first, last], "courts": [], "non_listing_marks": [], "issues": []}
    court = group = current = None
    with pdfplumber.open(pdf_path, pages=list(range(first, last + 1))) as pdf:
        for page in pdf.pages:
            number, height = page.page_number, page.height
            lines = [line for line in positioned_lines(page) if not line_text(line).isdigit()]
            page.close()
            index = 0
            while index < len(lines):
                line = lines[index]
                source = source_line(line, number)
                text = source["text"]
                index += 1
                if text.upper() in COURTS:
                    court = {"heading": source, "details": [], "groups": [], "records": []}
                    result["courts"].append(court)
                    group = current = None
                    continue
                if court is None:
                    result["issues"].append({**source, "reason": "Line before the first court heading"})
                    continue
                if group is None and current is None and source["top"] < height * .17:
                    court["details"].append(source)
                    continue
                if text == "`":
                    result["non_listing_marks"].append(source)
                    continue
                roster, division = ROSTER.fullmatch(text), DIVISION.match(text)
                if roster or division:
                    match = roster or division
                    heading_words = line if roster else line[:len(match[1].split())]
                    group = {"heading": source_line(heading_words, number), "details": [], "records": []}
                    if roster:
                        group.update(shared_role="Associate Justice", locality=roster[2])
                    elif len(heading_words) < len(line):
                        group["details"].append(source_line(line[len(heading_words):], number))
                    court["groups"].append(group)
                    current = None
                    continue
                if group and index < len(lines) and bold(line[0]):
                    consumed = read_member_pair(lines, index - 1, number, group)
                    if consumed:
                        index += consumed - 1
                        current = None
                        continue
                if bold(line[0]) and not (is_contact_line(text) or ROLE.fullmatch(text)):
                    name = list(takewhile(lambda word: bold(word) or word["text"] == ".", line))
                    current = new_record(name, number, group if group is not None else court)
                    line = line[len(name):]
                    if not line:
                        continue
                    source = source_line(line, number)
                if current is not None:
                    if is_contact_line(source["text"]):
                        current["contact_lines"].append(source)
                    elif ROLE.fullmatch(source["text"]):
                        current["role_lines"].append(source)
                    else:
                        result["issues"].append({**source, "reason": "Unclassified listing detail"})
                elif group is not None and is_contact_line(source["text"]):
                    group["details"].append(source)
                else:
                    result["issues"].append({**source, "reason": "Line without a clear owner"})
    titles = [court["heading"]["text"].upper() for court in result["courts"]]
    if len(titles) != len(COURTS) or set(titles) != COURTS:
        result["issues"].append({"reason": "Expected one heading for each court", "observed": titles})
    for court in result["courts"]:
        for owner in [court, *court["groups"]]:
            for record in owner["records"]:
                printed_role = " ".join(line["text"] for line in record["role_lines"])
                record["role"] = printed_role or owner.get("shared_role")
                if not record["role"]:
                    result["issues"].append({**record["name_source"], "reason": "Listing has no role"})
                if "\ufffd" in record["printed_name"]:
                    result["issues"].append({**record["name_source"], "reason": "Unmapped glyph in name"})
    return result
