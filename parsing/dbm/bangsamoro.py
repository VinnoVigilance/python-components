"""Extract the Bangsamoro and predecessor autonomous-region listings."""

import re
from collections import Counter
from itertools import groupby

import pdfplumber
import pypdfium2

from .layout import bold, line_text, positioned_lines
from .representatives import find_sections, split_columns
from .common import VACANCIES, is_contact


LEAD_ROLES = {"Chief Minister", "Regional Governor"}
GROUPS = {"regional secretaries", "heads of offices", "ministers", "ministries", "services/offices", "attached offices"}
ASSEMBLIES = {"REGIONAL LEGISLATIVE ASSEMBLY", "BANGSAMORO PARLIAMENT", "BANGSAMORO TRANSITION AUTHORITY"}
DISTRICT = re.compile(r"(?:\d+(?:st|nd|rd|th)|Lone) District", re.IGNORECASE)
NUMBER = re.compile(r"\d+\.")


def find_pages(pdf_path):
    last = None
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for number in range(find_sections(pdf_path)[0] - 1, 0, -1):
            page = pdf[number - 1]
            text_page = page.get_textpage()
            lines = [" ".join(line.split()) for line in text_page.get_text_range().splitlines()]
            text_page.close()
            page.close()
            body = [line for line in lines if line and line.lower() != "blank"
                    and not all(token.isdecimal() or token == "PB" for token in line.split())]
            if not body or any("DIPLOMATIC" in line.upper() for line in body[:6]):
                last = None
                continue
            if last is None:
                last = number
            if LEAD_ROLES.intersection(body) and any(
                "bangsamoro.gov.ph" in line.lower() or "armm.gov.ph" in line.lower() for line in body
            ):
                return number, last
    raise ValueError("Could not locate the autonomous-region directory")


def listing(result, name, location, group=None):
    record = {
        **location, "printed_name": name, "vacant": name.upper() in VACANCIES,
        "group": group, "office": None, "role_lines": [], "contact_lines": [],
    }
    result["records"].append(record)
    return record


def read_assembly(lines, heading, result, number, width):
    """Read assembly leadership and, where present, numbered district rosters."""
    assembly = {"heading": heading, "details": []}
    result["assemblies"].append(assembly)
    provinces = districts = None
    for line in lines:
        location = {"source_page": number, "top": round(line[0]["top"], 2)}
        text = line_text(line)
        if NUMBER.fullmatch(line[0]["text"]):
            entries = []
            for word in line:
                if NUMBER.fullmatch(word["text"]):
                    entries.append([word])
                elif entries:
                    entries[-1].append(word)
            for entry in entries:
                column = int(entry[0]["x0"] >= width / 2)
                if districts is None or not line_text(entry[1:]):
                    result["issues"].append({**location, "text": text, "reason": "Unresolved district member"})
                    continue
                record = listing(result, line_text(entry[1:]), location, heading)
                record.update(province=provinces[column], district=districts[column], roster_number=entry[0]["text"])
            continue
        if DISTRICT.search(text):
            districts = DISTRICT.findall(text)
            if len(districts) == 2 and provinces:
                continue
            result["issues"].append({**location, "text": text, "reason": "Unresolved district heading"})
            districts = None
            continue
        if bold(line[0]):
            name_words = next(groupby(line, key=bold))[1]
            name = list(name_words)
            role = line[len(name):]
            if role:
                record = listing(result, line_text(name), location, heading)
                record["role_lines"].append({**location, "text": line_text(role)})
            else:
                left = [word for word in line if word["x0"] < width / 2]
                right = line[len(left):]
                provinces = [line_text(left), line_text(right)] if left and right else [text, text]
                districts = None
            continue
        assembly["details"].append({**location, "text": text})


def read(pdf_path):
    first, last = find_pages(pdf_path)
    result = {
        "source": str(pdf_path), "pages": [first, last], "heading": None,
        "regional_details": [], "assemblies": [], "records": [], "issues": [],
    }
    group = current = None
    with pdfplumber.open(pdf_path, pages=list(range(first, last + 1))) as pdf:
        for page in pdf.pages:
            number, width = page.page_number, page.width
            lines = positioned_lines(page)
            page.close()
            if number == first:
                lead_role = next(index for index, line in enumerate(lines) if line_text(line) in LEAD_ROLES)
                heading_lines = 2 if line_text(lines[1]) == "IN MUSLIM MINDANAO" else 1
                result["heading"] = " ".join(map(line_text, lines[:heading_lines]))
                result["regional_details"] = [
                    {"source_page": number, "top": round(line[0]["top"], 2), "text": line_text(line)}
                    for line in lines[heading_lines:lead_role - 1]
                ]
                lead_name = lines[lead_role - 1]
                current = listing(result, line_text(lead_name),
                                  {"source_page": number, "top": round(lead_name[0]["top"], 2)})
                lines = lines[lead_role:]
            assembly_start = next(
                (index for index, line in enumerate(lines) if line_text(line) in ASSEMBLIES), None
            )
            executive = lines[:assembly_start]
            right_edge = Counter(round(line[-1]["x1"]) for line in executive).most_common(1)
            for line in executive:
                location = {"source_page": number, "top": round(line[0]["top"], 2)}
                text = line_text(line)
                if text.lower().replace("offi ces", "offices") in GROUPS:
                    group, current = text, None
                    continue
                on_left = line[0]["x0"] < width * .15
                vacant = line[0]["text"].upper() in VACANCIES
                name_start = on_left and (bold(line[0]) or vacant)
                if name_start:
                    name = [line[0]] if vacant else list(next(groupby(line, key=bold))[1])
                    office = line_text(line[len(name):])
                    current = listing(result, line_text(name), location, group)
                    if office and not is_contact(office):
                        current["office"] = office
                    elif office:
                        current["contact_lines"].append({**location, "text": office})
                    continue
                if current is None:
                    result["issues"].append({**location, "text": text, "reason": "Line without official"})
                    continue
                if number == first and current is result["records"][0]:
                    role, contact = ("", text) if is_contact(text) else (text, "")
                elif on_left:
                    left, right = split_columns(line, line[0]["x0"], right_edge[0][0])
                    if not right:
                        for index in range(1, len(line)):
                            gap = line[index]["x0"] - line[index - 1]["x1"]
                            if gap >= 8 and is_contact(line_text(line[index:])):
                                left, right = line[:index], line[index:]
                                break
                    role, contact = line_text(left), line_text(right)
                else:
                    role, contact = "", text
                if role:
                    current["role_lines"].append({**location, "text": role})
                if contact:
                    current["contact_lines"].append({**location, "text": contact})
            if assembly_start is not None:
                read_assembly(lines[assembly_start + 1:], line_text(lines[assembly_start]), result, number, width)
                group = current = None
    for record in result["records"]:
        if not (record["role_lines"] or record["group"] or record["vacant"]):
            result["issues"].append({
                "source_page": record["source_page"], "top": record["top"], "text": record["printed_name"],
                "reason": "Official without role or group",
            })
    return result
