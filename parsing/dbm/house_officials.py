"""Extract the House leadership and secretariat from the DBM directory."""

import re
from contextlib import closing

import pdfplumber
import pypdfium2

from .layout import PAGINATION, bold, positioned_range, source_line
from .house import HOUSE, MEMBERS, SECRETARIAT, find_house_boundaries
from .common import VACANCIES, contact


HOUSE_ROOT = "HOUSE OF REPRESENTATIVES"
GROUP_ROLES = {
    "deputy speakers": "Deputy Speaker",
    "majority leader": "Majority Leader",
    "minority leader": "Minority Leader",
    "secretary general": "Secretary General",
    "secreatary general": "Secretary General",
    "sergeant-at-arms": "Sergeant-at-Arms",
}
TITLE = re.compile(
    r"(?P<role>(?:Acting |OIC[- ])?(?:Speaker|(?:Deputy )?Secretary General"
    r"(?:/Chief of Staff)?|Sergeant-at-Arms))(?=\s|$)",
    re.IGNORECASE,
)


def secretariat_end(pdf_path, first):
    """Find the first divider after the secretariat without dropping continuation pages."""
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for index in range(first, len(pdf)):
            with closing(pdf[index]) as page, closing(page.get_textpage()) as text_page:
                lines = [" ".join(line.split()) for line in text_page.get_text_range().splitlines()]
            body = [line.upper() for line in lines if line and not PAGINATION.fullmatch(line)]
            if not body or body == ["BLANK"] or " ".join(body).startswith("JUDICIAL BRANCH"):
                return index
    raise ValueError("Could not locate the end of the House secretariat")




def read_section(lines, heading, result):
    leadership = heading == HOUSE
    section = {"heading": None, "header_lines": [], "group_headings": [], "records": []}
    group = None
    record = None
    root = False
    for page, words in lines:
        src = source_line(words, page)
        value = src["text"]
        if leadership and value == MEMBERS:
            break
        if value == heading:
            section["heading"] = src
            continue
        if section["heading"] is None:
            if leadership and value == HOUSE_ROOT:
                root = True
            if root:
                section["header_lines"].append(src)
            continue

        group_role = GROUP_ROLES.get(value.rstrip(":").casefold()) if leadership else None
        if group_role:
            group = (group_role, src)
            section["group_headings"].append(src)
            record = None
            continue

        title = TITLE.match(value)
        if title and record:
            role = title["role"]
            role_words = len(role.split())
            record["role"] = role
            record["role_source"] = source_line(words[:role_words], page)
            if words[role_words:]:
                details = source_line(words[role_words:], page)
                if contact(details["text"]):
                    record["contact_lines"].append(details)
                else:
                    result["issues"].append({**details, "reason": "Unrecognized detail after House title"})
            continue
        if contact(value):
            if record:
                record["contact_lines"].append(src)
            elif not leadership and not section["records"]:
                section["header_lines"].append(src)
            else:
                result["issues"].append({**src, "reason": "House contact without a listing"})
            continue

        name_end = next((i for i, word in enumerate(words) if not bold(word)), len(words))
        if not name_end and leadership and group:
            # Newer editions print group members in regular type. Their contact starts the next field.
            name_end = next((i for i in range(1, len(words)) if contact(words[i]["text"])), 0)
        if name_end:
            name = source_line(words[:name_end], page)
            record = {
                "printed_name": name["text"], "name_source": name,
                "vacant": name["text"].upper() in VACANCIES,
                "role": group[0] if group else None, "role_source": group[1] if group else None,
                "office_lines": [], "contact_lines": [],
            }
            section["records"].append(record)
            if words[name_end:]:
                details = source_line(words[name_end:], page)
                if contact(details["text"]):
                    record["contact_lines"].append(details)
                elif not leadership:
                    record["office_lines"].append(details)
                else:
                    result["issues"].append({**details, "reason": "Unrecognized detail after House leader"})
            continue
        result["issues"].append({**src, "reason": "Unrecognized House official line"})
    else:
        if leadership:
            result["issues"].append({"text": MEMBERS, "reason": "Missing positioned House Members boundary"})

    if not section["heading"] or not section["records"]:
        result["issues"].append({"text": heading, "reason": "Missing House officials section or listings"})
    for record in section["records"]:
        if not record["role"]:
            result["issues"].append({**record["name_source"], "reason": "House listing has no resolved role"})
    return section


def read(pdf_path):
    first, members, _, secretariat = find_house_boundaries(pdf_path)
    last = secretariat_end(pdf_path, secretariat)
    result = {"pdf": str(pdf_path), "issues": []}
    with pdfplumber.open(pdf_path) as pdf:
        result["leadership"] = read_section(positioned_range(pdf, first, members), HOUSE, result)
        result["secretariat"] = read_section(positioned_range(pdf, secretariat, last), SECRETARIAT, result)
    return result
