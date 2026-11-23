"""Read DBM missions and organizations with two-column representative listings."""

import re
from collections import Counter

import pdfplumber
import pypdfium2
from pdfplumber.utils import cluster_objects

from .layout import bold, line_text
from .common import VACANCIES


DIPLOMATIC = {"DIPLOMATIC MISSIONS", "DIPLOMATIC AND CONSULAR MISSIONS"}
CONSULAR = "CONSULATES AND CONSULAR MISSIONS"
INTERNATIONAL = {"INTERNATIONAL ORGANIZATIONS", "AND OTHER INTERNATIONAL ORGANIZATIONS"}
MISSION_HEADINGS = DIPLOMATIC | {CONSULAR, "COUNTRY REPRESENTATIVE"}
MISSION = re.compile(r"\b(?:consulate|embassy|nunciature)\b", re.IGNORECASE)
ORGANIZATION_HEADINGS = INTERNATIONAL | {"UNITED NATIONS AGENCIES", "UNITED NATIONS AGENCIES AND OTHER"}
END_MARKER = re.compile(r"^(?:PRODUCED BY:|PRIORITIES:|DATE:|A PROJECT OF THE BUDGET INFORMATION)")
OFFICE_LABEL = re.compile(
    r"^(?:REGIONAL CENT(?:ER|RE) FOR\b|[\w ]+ DEPARTMENT$|[\w ]+ REPRESENTATION IN\b)",
    re.IGNORECASE,
)


def find_sections(pdf_path):
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        international = consular = None
        for number in range(len(pdf), 0, -1):
            page = pdf[number - 1]
            page_text = page.get_textpage()
            lines = {" ".join(line.upper().split()) for line in page_text.get_text_range().splitlines()}
            page_text.close()
            page.close()
            if consular and lines & DIPLOMATIC and "COUNTRY REPRESENTATIVE" in lines:
                return number, consular, international - 1
            if international and CONSULAR in lines:
                consular = number
            elif not consular and lines & INTERNATIONAL:
                international = number
    raise ValueError("Could not locate the diplomatic, consular, and international body headings")


def split_columns(line, left_edge, right_edge):
    if abs(line[-1]["x1"] - right_edge) > 2:
        return line, []
    if line[0]["x0"] > left_edge + 4:
        return [], line
    for index in range(len(line) - 1, 0, -1):
        previous, word = line[index - 1:index + 1]
        if (bold(word) and not bold(previous)) or word["x0"] - previous["x1"] >= 8:
            return line[:index], line[index:]
    return line, []


def representative_columns(pdf_path, first, last, headings, footer_tokens):
    """Yield each page's positioned left and right words."""
    with pdfplumber.open(pdf_path, pages=list(range(first, last + 1))) as pdf:
        for page in pdf.pages:
            lines = []
            for words in cluster_objects(page.extract_words(extra_attrs=["fontname"]), "top", 2):
                line = sorted((word for word in words if 0 <= word["x0"] < page.width), key=lambda w: w["x0"])
                if not line or line_text(line).upper() in headings:
                    continue
                if line[0]["top"] >= page.height * .95 and all(
                    word["text"].isdigit() or word["text"] in footer_tokens for word in line
                ):
                    continue
                lines.append(line)
            page.close()
            left = Counter(round(line[0]["x0"]) for line in lines)
            right = Counter(round(line[-1]["x1"]) for line in lines)
            if not left or right.most_common(1)[0][1] < 2:
                yield page.page_number, None
                continue
            left_edge = left.most_common(1)[0][0]
            right_edge = right.most_common(1)[0][0]
            yield page.page_number, [
                (
                    {"source_page": page.page_number, "top": round(line[0]["top"], 2)},
                    *split_columns(line, left_edge, right_edge),
                )
                for line in lines
            ]


def flag_missing_roles(representatives, issues):
    for person in representatives:
        if not person["role_lines"] and not person["vacant"]:
            issues.append({
                "source_page": person["source_page"], "top": person["top"],
                "text": person["printed_name"], "reason": "Representative without role",
            })


def read_missions(pdf_path):
    first, consular_start, last = find_sections(pdf_path)
    result = {"source": str(pdf_path), "pages": [first, last], "missions": [], "issues": []}
    country = mission = representative = None
    for number, lines in representative_columns(pdf_path, first, last, MISSION_HEADINGS, set()):
        if number == consular_start:
            country = mission = representative = None
        section = "consular" if number >= consular_start else "diplomatic"
        if lines is None:
            result["issues"].append({"source_page": number, "reason": "Could not establish column alignment"})
            country = mission = representative = None
            continue
        for location, left_words, right_words in lines:
            left_text, right_text = line_text(left_words), line_text(right_words)
            name_start = bool(right_words) and (bold(right_words[0]) or right_text.upper() in VACANCIES)
            if left_text and name_start and left_text.isupper() and not MISSION.search(left_text):
                country = left_text
                mission = representative = None
            name_continues = (
                name_start and representative is not None and not representative["vacant"]
                and not representative["role_lines"] and right_text.isupper()
                and right_text.upper() not in VACANCIES
                and representative["source_page"] == number
                and location["top"] - representative["top"] <= 13
                and mission["printed_mission"] is None
            )
            # Some offices print the name one line above the mission heading.
            # Move only that fresh, role-less representative to the new office;
            # a branch continuation without a new name keeps its existing owner.
            delayed_heading = (
                mission is not None and representative is not None and not name_start
                and not representative["role_lines"] and representative["source_page"] == number
                and 0 < location["top"] - representative["top"] <= 13
                and bool(MISSION.search(left_text)) and "@" not in left_text
                and bool(mission["printed_mission"])
                and (mission["source_page"] != number or representative["top"] > mission["top"])
            )
            if mission is None or delayed_heading or (name_start and not name_continues and MISSION.search(left_text)):
                if country is None:
                    result["issues"].append({
                        **location, "text": line_text(left_words + right_words), "reason": "Missing country heading",
                    })
                    continue
                carried = mission["representatives"].pop() if delayed_heading else None
                mission = {
                    **location, "section": section, "country": country, "printed_mission": None,
                    "mission_lines": [], "representatives": [],
                }
                result["missions"].append(mission)
                if carried:
                    mission["representatives"].append(carried)
                if left_text == country and name_start:
                    left_text = ""
            if left_text:
                if mission["printed_mission"] is None:
                    mission["printed_mission"] = left_text
                elif not mission["mission_lines"] and left_text.lower().startswith("of "):
                    mission["printed_mission"] += "\n" + left_text
                    mission.setdefault("mission_name_continuation_lines", []).append({**location, "text": left_text})
                else:
                    mission["mission_lines"].append({**location, "text": left_text})
            if name_continues:
                representative["printed_name"] += "\n" + right_text
                representative["name_continuations"].append(location)
            elif name_start:
                representative = {
                    **location, "printed_name": right_text, "vacant": right_text.upper() in VACANCIES,
                    "name_continuations": [], "role_lines": [],
                }
                mission["representatives"].append(representative)
            elif right_text:
                if representative is None:
                    result["issues"].append({**location, "text": right_text, "reason": "Role without representative"})
                else:
                    representative["role_lines"].append({**location, "text": right_text})
    for mission in result["missions"]:
        if not mission["printed_mission"] or not mission["representatives"]:
            result["issues"].append({
                "source_page": mission["source_page"], "country": mission["country"],
                "reason": "Incomplete mission",
            })
        flag_missing_roles(mission["representatives"], result["issues"])
    return result


def find_international_pages(pdf_path):
    first = find_sections(pdf_path)[2] + 1
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for index in range(first, len(pdf)):
            page = pdf[index]
            text_page = page.get_textpage()
            lines = [" ".join(line.upper().split()) for line in text_page.get_text_range().splitlines() if line.strip()]
            text_page.close()
            page.close()
            if not lines or any(END_MARKER.match(line) for line in lines):
                return first, index
        return first, len(pdf)


def organization_heading(text):
    return text.isupper() and text[0].isalpha() and "@" not in text and not text.startswith(("WWW.", "HTTP"))


def read_organizations(pdf_path):
    first, last = find_international_pages(pdf_path)
    result = {"source": str(pdf_path), "pages": [first, last], "organizations": [], "issues": []}
    organization = representative = None
    for number, lines in representative_columns(pdf_path, first, last, ORGANIZATION_HEADINGS, {"PB"}):
        if lines is None:
            result["issues"].append({"source_page": number, "reason": "Could not establish column alignment"})
            organization = representative = None
            continue
        previous_top = None
        for location, left_words, right_words in lines:
            left, right = line_text(left_words), line_text(right_words)
            vacant = right.upper() in VACANCIES
            name_start = bool(right_words) and (bold(right_words[0]) or vacant)
            top = location["top"]
            block_start = previous_top is None or top - previous_top > 15
            previous_top = top
            if block_start and organization_heading(left):
                organization = {
                    **location, "printed_organization": left, "printed_office": None,
                    "office_lines": [], "representatives": [],
                }
                result["organizations"].append(organization)
                representative = None
                left = ""
            if organization is None:
                result["issues"].append({**location, "text": line_text(left_words + right_words),
                                         "reason": "Listing without organization heading"})
                continue
            if left:
                if not organization["office_lines"] and not organization["printed_office"] and (
                    (left.startswith("(") and left.endswith(")") and left[1:2].isalpha())
                    or OFFICE_LABEL.match(left)
                ):
                    organization["printed_office"] = left
                elif not organization["office_lines"] and not organization["printed_office"] and (
                    not name_start and organization_heading(left) and not block_start
                ):
                    organization["printed_organization"] += "\n" + left
                else:
                    organization["office_lines"].append({**location, "text": left})
            if name_start:
                representative = {**location, "printed_name": right, "vacant": vacant, "role_lines": []}
                organization["representatives"].append(representative)
            elif right:
                if representative is None:
                    result["issues"].append({**location, "text": right, "reason": "Role without representative"})
                else:
                    representative["role_lines"].append({**location, "text": right})

    for organization in result["organizations"]:
        if not organization["representatives"]:
            result["issues"].append({
                "source_page": organization["source_page"], "text": organization["printed_organization"],
                "reason": "Organization listing without representative",
            })
        flag_missing_roles(organization["representatives"], result["issues"])
    return result
