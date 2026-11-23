"""Extract executive office listings from the DBM directory."""

import re
from collections import Counter
from contextlib import closing

import pdfplumber
import pypdfium2

from .layout import bold, line_text, positioned_range, source_line
from .column_roster import read_location_official_contact_columns
from .common import EMAIL, VACANCIES, contact


PRESIDENCY = "OFFICE OF THE PRESIDENT"
VICE_PRESIDENCY = "OFFICE OF THE VICE PRESIDENT"
COMMUNICATIONS = {"PRESIDENTIAL COMMUNICATIONS", "PRESIDENTIAL COMMUNICATIONS OFFICE",
                  "PRESIDENTIAL COMMUNICATIONS OPERATIONS OFFICE"}
OFFICE_SECTIONS = {
    "president": ({PRESIDENCY}, {"PRESIDENT"}, {VICE_PRESIDENCY}),
    "vice-president": ({VICE_PRESIDENCY}, {"VICE PRESIDENT"}, COMMUNICATIONS),
    "presidential-communications": (
        COMMUNICATIONS, {"SECRETARY", "ACTING SECRETARY"}, {"OTHER EXECUTIVE OFFICES"},
    ),
    "other-executive-offices": (
        {"OTHER EXECUTIVE OFFICES"}, set(), {"DEPARTMENT OF AGRARIAN REFORM", "DEPARTMENT OF AGRICULTURE"},
    ),
    "agrarian-reform": (
        {"DEPARTMENT OF AGRARIAN REFORM"}, {"SECRETARY", "ACTING SECRETARY", "OIC-SECRETARY"},
        {"DEPARTMENT OF AGRICULTURE", "DEPARTMENT OF BUDGET AND MANAGEMENT"},
    ),
    "agriculture": (
        {"DEPARTMENT OF AGRICULTURE"}, {"SECRETARY", "ACTING SECRETARY", "CONCURRENT SECRETARY"},
        {"DEPARTMENT OF AGRARIAN REFORM", "DEPARTMENT OF BUDGET AND MANAGEMENT"},
    ),
}
SECRETARIAT_GROUP_ROLES = {
    "UNDERSECRETARIES": "Undersecretary",
    "ASSISTANT SECRETARIES": "Assistant Secretary",
}
PUBLIC_ASSISTANCE_HEADING = "PUBLIC ASSISTANCE DESK/EXTERNAL RELATIONS OFFICE"
ASSISTANCE_CENTER = re.compile(r"\b(?:ASSISTANCE|COMPLAINTS?|CONTACT) CENT(?:ER|RE)$")
COMMUNICATIONS_AGENCY_GROUPS = {"BUREAUS", "ATTACHED AGENCY", "ATTACHED AGENCIES"}
AGENCY_RELATIONSHIP_HEADINGS = COMMUNICATIONS_AGENCY_GROUPS | {
    "UNDER SUPERVISION AND CONTROL", "UNDER ADMINISTRATIVE SUPERVISION",
}
DEPARTMENT_TITLES = {
    "SECRETARY", "ACTING SECRETARY", "CONCURRENT SECRETARY", "OIC-SECRETARY",
    "OFFICER-IN-CHARGE", "OFFI CER-IN-CHARGE",
}
CONSTITUTIONAL_BOUNDARIES = {"CONSTITUTIONAL OFFICES", "CIVIL SERVICE COMMISSION"}
CONSTITUTIONAL_OFFICE_HEADINGS = (
    "CIVIL SERVICE COMMISSION", "COMMISSION ON AUDIT", "COMMISSION ON ELECTIONS",
    "COMMISSION ON HUMAN RIGHTS", "OFFICE OF THE OMBUDSMAN",
)
CONSTITUTIONAL_LEAD_TITLES = {"CHAIRPERSON", "CHAIRMAN", "OMBUDSMAN", "OFFICER-IN-CHARGE"}
CORPORATIONS_DIVIDER = re.compile(r"\bGOCC\S* AND SUC\S*\b")
CORPORATIONS_INTRO = "GOVERNMENT-OWNED AND/OR -CONTROLLED CORPORATIONS"
CORPORATIONS_FIRST_AGENCY = "AL-AMANAH ISLAMIC INVESTMENT BANK OF THE PHILIPPINES"
CORPORATIONS_HEADING = re.compile(r"GOVERNMENT-OWNED AND/OR\s*-CONTROLLED CORPORATIONS")
CORPORATION_CHAIR_ROLE = re.compile(r"^(?:ACTING )?CHAIR(?:MAN|PERSON)\b")
COLLEGES_HEADING = "STATE UNIVERSITIES AND COLLEGES"
COLLEGE_REGION_HEADING = re.compile(
    r"REGION [IVXLCDM]+\b.*|NATIONAL CAPITAL REGION|CORDILLERA ADMINISTRATIVE REGION|"
    r"MIMAROPA(?: REGION)?|NEGROS ISLAND REGION|(?:BANGSAMORO )?AUTONOMOUS REGION IN MUSLIM MINDANAO"
)
UPPERCASE_NAME_WORD = re.compile(r"[^\W\d_]+(?:[.'’\-][^\W\d_]+)*[.,]?$")
ROSTER_ROLE = re.compile(r"(?:(?:ACTING|DEPUTY|ASSISTANT|REGIONAL|EXECUTIVE) )*"
                         r"(?:CHAIRPERSON|CHAIRMAN|COMMISSIONER|DIRECTOR|OMBUDSMAN)|"
                         r"(?:OIC-)?DIRECTOR, .+|OFFICER-IN-CHARGE(?:, .+)?")
PLANNING_AUTHORITY_HEADINGS = {"NATIONAL ECONOMIC", "NATIONAL ECONOMIC AND"}
OVERSEAS_ROSTER_HEADINGS = {
    "PHILIPPINE FOREIGN SERVICE ESTABLISHMENTS/POSTS", "PHILIPPINE MISSIONS", "MIGRANT WORKERS OFFICES",
}
SCIENCE_GROUPS = {
    "COLLEGIAL AND SCIENTIFIC BODIES", "RESEARCH AND DEVELOPMENT INSTITUTES",
    "SECTORAL PLANNING COUNCILS", "SCIENTIFIC AND TECHNOLOGICAL SERVICES",
}
CONTACT_LABEL = re.compile(
    r"^(?:loc\b|trunkline\b|tel\b|fax\b|e-?mail\b|hours of operations?\b|(?:\w+ )?hotline\s*:)", re.I
)
ADDRESS = re.compile(r"\b(?:city|manila|street|village|st\.)\s*$|^\d+/F\b", re.I)


def find_pages(pdf_path, office="president"):
    headings, titles, boundary = OFFICE_SECTIONS[office]
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        first = None
        for index in range(len(pdf)):
            with closing(pdf[index]) as page, closing(page.get_textpage()) as text_page:
                lines = {" ".join(line.upper().split()) for line in text_page.get_text_range().splitlines()}
            if first is None and headings.intersection(lines) and (not titles or titles.intersection(lines)):
                first = index + 1
            if first is not None and boundary.intersection(lines):
                return first, index + 1
    raise ValueError(f"Could not locate {office} and the following section")


def is_contact(value):
    if "@" in value and not CONTACT_LABEL.match(value):
        # Preserve printed email fragments without consuming a preceding title.
        return "@" in value.partition(" ")[0] or (
            value == value.lower() and bool(EMAIL.fullmatch("".join(value.split())))
        )
    return bool(CONTACT_LABEL.match(value) or contact(value))


def append_right_detail(record, detail, *, address_column=False):
    text = detail["text"]
    if ADDRESS.search(text):
        field = "address_lines"
    elif is_contact(text):
        field = "contact_lines"
    elif address_column:
        field = "address_lines"
    else:
        field = "office_lines"
    record[field].append(detail)


def right_column_start(words):
    return next(
        (i for i in range(1, len(words)) if words[i]["x0"] - words[i - 1]["x1"] >= 20),
        len(words),
    )


def infer_name_before_role(page, words, next_row, margin):
    """Recognize an unbolded uppercase name followed by a bold, aligned title."""
    next_page, next_words = next_row
    if (next_page != page or abs(words[0]["x0"] - margin) > 4
            or abs(next_words[0]["x0"] - words[0]["x0"]) > 2
            or not 0 < next_words[0]["top"] - words[0]["top"] <= 16):
        return 0
    left_end = right_column_start(words)
    left = words[:left_end]
    if len(left) < 2 or any(bold(word) or not word["text"].isupper()
                            or not UPPERCASE_NAME_WORD.fullmatch(word["text"]) for word in left):
        return 0
    title = next_words[:right_column_start(next_words)]
    if all(map(bold, title)) and ROSTER_ROLE.fullmatch(line_text(title).upper()):
        return left_end
    return 0


def read_role(page, words, record, *, address_column=False, uppercase_office=False):
    """Read a left title and optional right detail with a clear column gap."""
    right_start = right_column_start(words)
    left, right = words[:right_start], words[right_start:]
    # An extension, phone, email or site can also follow the title at ordinary word spacing.
    contact_start = next((i for i in range(1, len(left)) if is_contact(line_text(left[i:]))), len(left))
    record["role_lines"].append(source_line(left[:contact_start], page))
    if contact_start < len(left):
        record["contact_lines"].append(source_line(left[contact_start:], page))
    if right:
        detail = source_line(right, page)
        if uppercase_office and detail["text"].isupper() and not is_contact(detail["text"]):
            record["office_lines"].append(detail)
        else:
            append_right_detail(record, detail, address_column=address_column)


def read_agency_roster(rows, margin, headings, result):
    """Read a roster with the official on the left and agency details on the right."""
    starts = [i for i, (_, words) in enumerate(rows) if line_text(words).upper() in headings]
    if len(starts) == 2:
        start, repeat = starts
        original, duplicate = rows[start:repeat], rows[repeat:]
        first = [(page - original[0][0], line_text(words)) for page, words in original]
        second = [(page - duplicate[0][0], line_text(words)) for page, words in duplicate]
        if first == second:
            result["repeated_page_ranges"] = [{
                "original": [original[0][0], original[-1][0]],
                "duplicate": [duplicate[0][0], duplicate[-1][0]],
            }]
            rows = rows[:repeat]

    record = None
    for index, (page, words) in enumerate(rows):
        src = source_line(words, page)
        value = src["text"]
        if value.upper() in headings:
            if result["heading"] is None:
                result["heading"] = src
            else:
                result.setdefault("heading_continuation_lines", []).append(src)
            record = None
            continue
        if result["heading"] is None:
            continue

        on_left = src["x"] <= margin + 8
        name_end = next((i for i, word in enumerate(words) if not bold(word)), len(words)) if on_left else 0
        if on_left and words[0]["text"].upper() in VACANCIES:
            name_end = 1
        contact_row = is_contact(value) or value.upper().startswith("TL:")
        next_on_left = index + 1 < len(rows) and rows[index + 1][1][0]["x0"] <= margin + 8
        unnamed = not on_left and not contact_row and value.isupper() and next_on_left
        if (unnamed and record and record["vacant"] and not record["office_lines"]
                and not record["role_lines"] and record["name_source"]["source_page"] == page):
            record["office_lines"].append(src)
            continue
        if name_end or unnamed:
            name = source_line(words[:name_end], page) if name_end else None
            record = {
                "printed_name": name["text"] if name else None, "name_source": name, "group_source": None,
                "vacant": bool(name and name["text"].upper() in VACANCIES),
                "role_lines": [], "office_lines": [], "address_lines": [], "contact_lines": [],
            }
            result["records"].append(record)
            if name_end < len(words):
                record["office_lines"].append(source_line(words[name_end:], page))
        elif record and not on_left and ADDRESS.search(value):
            record["address_lines"].append(src)
        elif record and contact_row:
            record["contact_lines"].append(src)
        elif record and on_left:
            read_role(page, words, record, address_column=True, uppercase_office=True)
        elif record:
            record["address_lines"].append(src)
        else:
            result["issues"].append({**src, "reason": "Unassigned Executive office line"})


def read(pdf_path, office="president"):
    if office == "departments":
        return read_departments(pdf_path)
    if office == "constitutional-offices":
        return read_constitutional_offices(pdf_path)
    if office == "corporations-and-colleges":
        return read_corporations_and_colleges(pdf_path)
    headings, titles, boundary = OFFICE_SECTIONS[office]
    first, last = find_pages(pdf_path, office)
    with pdfplumber.open(pdf_path) as pdf:
        rows = []
        for page, words in positioned_range(pdf, first, last):
            if line_text(words).upper() in boundary:
                break
            rows.append((page, words))
        else:
            return parse_rows(pdf_path, rows, headings, titles, office, boundary_found=False)
    return parse_rows(pdf_path, rows, headings, titles, office)


def read_constitutional_offices(pdf_path):
    """Find the office blocks and pass each to the shared person roster reader."""
    listing_pages = []
    started = False
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for index in range(len(pdf)):
            with closing(pdf[index]) as page, closing(page.get_textpage()) as text_page:
                lines = [" ".join(line.upper().split()) for line in text_page.get_text_range().splitlines()
                         if line.strip()]
            if not started:
                started = (CONSTITUTIONAL_OFFICE_HEADINGS[0] in lines
                           and bool(CONSTITUTIONAL_LEAD_TITLES.intersection(lines)))
                if not started:
                    continue
            body = " ".join(lines)
            if (CORPORATIONS_DIVIDER.search(body) or CORPORATIONS_INTRO in body
                    or CORPORATIONS_FIRST_AGENCY in body):
                break
            if lines and lines != ["BLANK"]:
                listing_pages.append(index + 1)
        else:
            raise ValueError("Could not locate the Constitutional Offices and their closing boundary")

    sections = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in listing_pages:
            for row in positioned_range(pdf, page, page):
                words = row[1]
                if line_text(words).upper() in CONSTITUTIONAL_OFFICE_HEADINGS and all(map(bold, words)):
                    sections.append([])
                if sections:
                    sections[-1].append(row)
            pdf.pages[page - 1].close()
    headings = tuple(line_text(rows[0][1]).upper() for rows in sections)
    if headings != CONSTITUTIONAL_OFFICE_HEADINGS:
        raise ValueError(f"Unexpected Constitutional Offices headings: {headings}")

    return {"pdf": str(pdf_path), "constitutional_offices": [
        parse_rows(pdf_path, rows, {heading}, CONSTITUTIONAL_LEAD_TITLES, "constitutional-offices")
        for heading, rows in zip(headings, sections)
    ]}


def read_corporations_and_colleges(pdf_path):
    """Locate the DBM sections in the body, then read their shared agency layout."""
    corporations_page = colleges_page = None
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for index in range(len(pdf)):
            with closing(pdf[index]) as page, closing(page.get_textpage()) as text_page:
                lines = [" ".join(line.upper().split()) for line in text_page.get_text_range().splitlines()
                         if line.strip()]
            if corporations_page is None and any(CORPORATION_CHAIR_ROLE.match(line) for line in lines):
                headings = lines + [f"{first} {second}" for first, second in zip(lines, lines[1:])]
                if any(CORPORATIONS_HEADING.fullmatch(heading) for heading in headings):
                    corporations_page = index + 1
            if (corporations_page and colleges_page is None and COLLEGES_HEADING in lines
                    and "NATIONAL CAPITAL REGION" in lines):
                colleges_page = index + 1
            if colleges_page and (
                "LEGISLATIVE BRANCH" in lines or "SENATE OF THE PHILIPPINES" in lines
                or any(pair == ("LEGISLATIVE", "BRANCH") for pair in zip(lines, lines[1:]))
            ):
                end_page = index + 1
                break
        else:
            raise ValueError("Could not locate the corporation and college rosters and their closing boundary")

    with pdfplumber.open(pdf_path) as pdf:
        corporation_rows = list(positioned_range(pdf, corporations_page, colleges_page - 1))
        college_rows = list(positioned_range(pdf, colleges_page, end_page - 1))

    heading_end = next(
        (index for index, (page, words) in enumerate(corporation_rows, start=1)
         if page == corporations_page and line_text(words).upper().endswith("-CONTROLLED CORPORATIONS")), None,
    )
    if heading_end is None:
        raise ValueError("Could not separate the corporation heading from the first listing")
    roster_rows = corporation_rows[heading_end:]
    margin = min(words[0]["x0"] for _, words in roster_rows)
    corporation_heading = [source_line(words, page) for page, words in corporation_rows[:heading_end]]
    corporations = {
        "heading": corporation_heading[0], "heading_continuation_lines": corporation_heading[1:],
        "records": [], "issues": [],
    }
    read_agency_roster(roster_rows, margin, set(), corporations)

    college_heading = []
    groups = []
    for page, words in college_rows:
        if (words[0]["x0"] > margin + 8 and all(map(bold, words))
                and COLLEGE_REGION_HEADING.fullmatch(line_text(words).upper())):
            groups.append({"heading": source_line(words, page), "records": [], "issues": [], "rows": []})
        elif groups:
            groups[-1]["rows"].append((page, words))
        else:
            college_heading.append(source_line(words, page))
    if not college_heading or not groups:
        raise ValueError("Could not separate the college heading from its regional groups")
    for group in groups:
        read_agency_roster(group.pop("rows"), margin, set(), group)
        if not group["records"]:
            group["issues"].append({**group["heading"], "reason": "Region has no college listings"})
    for section in [corporations, *groups]:
        for record in section["records"]:
            if not record["office_lines"]:
                source = record["name_source"] or section["heading"]
                section["issues"].append({**source, "reason": "Missing institution"})

    return {
        "pdf": str(pdf_path),
        "corporations": corporations,
        "colleges": {"heading_lines": college_heading, "groups": groups},
        "issues": corporations["issues"] + [issue for group in groups for issue in group["issues"]],
    }


def read_departments(pdf_path):
    """Find each department in source order and apply the shared roster reader."""
    _, first = find_pages(pdf_path, "other-executive-offices")
    roster_pages = []
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for index in range(first - 1, len(pdf)):
            with closing(pdf[index]) as page, closing(page.get_textpage()) as text_page:
                lines = [" ".join(line.upper().split()) for line in text_page.get_text_range().splitlines()
                         if line.strip()]
            if CONSTITUTIONAL_BOUNDARIES.intersection(lines) or any(
                left == "CONSTITUTIONAL" and right == "OFFICES" for left, right in zip(lines, lines[1:])
            ):
                break
            if lines and lines != ["BLANK"]:
                roster_pages.append(index + 1)
        else:
            raise ValueError("Could not locate the constitutional offices after the department rosters")
    sections = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in roster_pages:
            for _, words in positioned_range(pdf, page, page):
                text = line_text(words)
                if (text.startswith("DEPARTMENT OF ") or text in PLANNING_AUTHORITY_HEADINGS
                        or text in OVERSEAS_ROSTER_HEADINGS) and text.isupper() and all(map(bold, words)):
                    sections.append([])
                if sections:
                    sections[-1].append((page, words))
            pdf.pages[page - 1].close()
    if not sections:
        raise ValueError("Could not locate any department roster headings")

    departments = []
    related_rosters = []
    column_rosters = []
    department_rows = None
    for rows in sections:
        heading = line_text(rows[0][1])
        if heading in OVERSEAS_ROSTER_HEADINGS:
            end = next((i for i, (_, words) in enumerate(rows[1:], 1)
                        if line_text(words).upper() in AGENCY_RELATIONSHIP_HEADINGS), len(rows))
            section = read_location_official_contact_columns(rows[1:end])
            section["heading"] = source_line(rows[0][1], rows[0][0])
            if department_rows:
                parent_page, parent_words = department_rows[0]
                section["parent_heading"] = source_line(parent_words, parent_page)
                department_rows.extend(rows[end:])
            column_rosters.append(section)
        elif heading in PLANNING_AUTHORITY_HEADINGS:
            titles = DEPARTMENT_TITLES | {"SOCIOECONOMIC PLANNING SECRETARY", "ACTING SOCIOECONOMIC PLANNING SECRETARY"}
            related_rosters.append(parse_rows(pdf_path, rows, {heading}, titles, "departments"))
        else:
            department_rows = rows.copy()
            departments.append(department_rows)
    return {
        "pdf": str(pdf_path),
        "departments": [
            parse_rows(pdf_path, rows, {line_text(rows[0][1])}, DEPARTMENT_TITLES, "departments")
            for rows in departments
        ],
        "related_rosters": related_rosters,
        "column_rosters": column_rosters,
    }


def parse_rows(pdf_path, rows, headings, titles, office, *, boundary_found=True):
    communications = office == "presidential-communications"
    person_roster = office in {"agrarian-reform", "agriculture", "departments", "constitutional-offices"}
    grouped_roles = SECRETARIAT_GROUP_ROLES if communications or person_roster else {}
    agency_groups = {
        "presidential-communications": COMMUNICATIONS_AGENCY_GROUPS,
        "agriculture": AGENCY_RELATIONSHIP_HEADINGS,
        "departments": AGENCY_RELATIONSHIP_HEADINGS,
        "constitutional-offices": AGENCY_RELATIONSHIP_HEADINGS,
    }.get(office, set())
    extra_groups = SCIENCE_GROUPS if "DEPARTMENT OF SCIENCE AND TECHNOLOGY" in headings else set()
    result = {
        "pdf": str(pdf_path), "heading": None, "header_lines": [], "group_headings": [],
        "records": [], "issues": [],
    }
    if not boundary_found:
        result["issues"].append({"reason": "Missing positioned Executive section boundary"})

    if not rows:
        raise ValueError(f"No positioned lines found in {office}")
    margin = Counter(round(words[0]["x0"]) for _, words in rows).most_common(1)[0][0]
    if office == "other-executive-offices":
        read_agency_roster(rows, margin, headings, result)
        if not result["heading"] or not result["records"]:
            result["issues"].append({"text": office, "reason": "Missing Executive office heading or listings"})
        for record in result["records"]:
            source = record["name_source"] or record["office_lines"][0]
            if not record["role_lines"]:
                result["issues"].append({**source, "reason": "Missing printed role"})
            if not record["office_lines"]:
                result["issues"].append({**source, "reason": "Missing agency name"})
        return result
    group = record = None
    inferred_role_row = None
    for index, (page, words) in enumerate(rows):
        src = source_line(words, page)
        value = src["text"]
        label = value.upper()
        if result["heading"] is None:
            if label in headings:
                result["heading"] = src
            continue
        if (communications and result["heading"]["text"].upper() == "PRESIDENTIAL COMMUNICATIONS"
                and not result["records"] and label == "OPERATIONS OFFICE"):
            result.setdefault("heading_continuation_lines", []).append(src)
            continue

        inferred_name_end = 0
        role_heading = label.removesuffix(":").rstrip()
        known_group = role_heading in grouped_roles or label in agency_groups or label in extra_groups
        if person_roster and not bold(words[0]) and not known_group and index + 1 < len(rows):
            inferred_name_end = infer_name_before_role(page, words, rows[index + 1], margin)
        officeholder = bool(inferred_name_end) or (
            index + 1 < len(rows) and line_text(rows[index + 1][1]).upper() in titles
        )
        if (office in {"departments", "constitutional-offices"} and not result["records"] and not result["header_lines"]
                and not officeholder and value.isupper() and all(map(bold, words))):
            result.setdefault("heading_continuation_lines", []).append(src)
            continue
        if not result["records"] and not officeholder:
            result["header_lines"].append(src)
            continue

        name_end = 0 if index == inferred_role_row else next(
            (i for i, word in enumerate(words) if not bold(word)), len(words)
        )
        # Even a heading that nearly fills the line is inset from the roster margin.
        centered = src["x"] > margin + 8
        agency = group and group["text"].upper() in agency_groups
        right_only = record and src["x"] > record["name_source"]["x"] + 4
        if person_roster and not centered and words[0]["text"].upper() in VACANCIES:
            name_end = 1
        elif inferred_name_end:
            name_end = inferred_name_end
        if known_group or (centered and name_end == len(words) and not officeholder and not is_contact(value)):
            group = src
            result["group_headings"].append(group)
            record = None
        elif agency and right_only and ADDRESS.search(value):
            record["address_lines"].append(src)
        elif is_contact(value) and record:
            record["contact_lines"].append(src)
        elif name_end:
            if inferred_name_end:
                inferred_role_row = index + 1
            name = source_line(words[:name_end], page)
            record = {
                "printed_name": name["text"], "name_source": name, "group_source": group,
                "vacant": name["text"].upper() in VACANCIES,
                "role_lines": [], "office_lines": [], "address_lines": [], "contact_lines": [],
            }
            if group:
                inherited_role = grouped_roles.get(group["text"].upper().removesuffix(":").rstrip())
                if inherited_role:
                    record["inherited_role"] = inherited_role
            result["records"].append(record)
            if name_end < len(words):
                append_right_detail(record, source_line(words[name_end:], page))
        elif (record and centered and record["address_lines"] and ADDRESS.search(value)
              and (not communications or right_only)):
            record["address_lines"].append(src)
        elif record:
            name_x = record["name_source"]["x"]
            if not centered or label in titles or ((communications or person_roster) and abs(src["x"] - name_x) <= 4):
                office_wrap = record["office_lines"] and record["office_lines"][-1]["text"].endswith("-")
                read_role(page, words, record, address_column=bool(agency and not office_wrap),
                          uppercase_office=bool(agency and person_roster))
            elif (communications or person_roster) and right_only:
                append_right_detail(record, src, address_column=bool(agency))
            else:
                result["issues"].append({**src, "reason": "Unassigned Executive office line"})
        else:
            result["issues"].append({**src, "reason": "Unassigned Executive office line"})

    if not result["heading"] or not result["records"]:
        result["issues"].append({"text": office, "reason": "Missing Executive office heading or listings"})
    people = []
    for record in result["records"]:
        group = record["group_source"]
        has_role = record["role_lines"] or record.get("inherited_role")
        if (person_roster and group and group["text"].upper().removesuffix(":") == PUBLIC_ASSISTANCE_HEADING
                and ASSISTANCE_CENTER.search(record["printed_name"].upper())
                and record["contact_lines"] and not record["office_lines"] and not has_role):
            result.setdefault("office_contacts", []).append({
                "heading": record["name_source"], "group_source": group,
                "address_lines": record["address_lines"], "contact_lines": record["contact_lines"],
            })
            continue
        people.append(record)
        if not has_role:
            result["issues"].append({**record["name_source"], "reason": "Missing printed role"})
    result["records"] = people
    return result
