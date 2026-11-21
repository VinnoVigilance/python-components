"""Read DBM local government tables and their continuation pages."""

import re
from collections import Counter
from dataclasses import dataclass

import pdfplumber
from pdfplumber.utils import cluster_objects

from .common import CONTACT, DOMAIN, EMAIL, EMAIL_START, VACANCIES, is_contact
from .layout import line_text, split_row_at_columns


@dataclass(frozen=True)
class TableLayout:
    title: str
    header: str
    place: str
    detail: str
    place_column: int
    roles: tuple[str, str]
    detail_pattern: str | None = None
    detail_required: bool = True
    header_tail: str | None = None
    detail_heading: re.Pattern | None = None
    header_pattern: re.Pattern | None = None

    def matches_header(self, text):
        if self.header_pattern:
            return self.header_pattern.fullmatch(text) is not None
        return text == self.header


REGION = re.compile(
    r"(?:REGION\b.*|.*\bREGION|MIMAROPA|(?:BANGSAMORO )?AUTONOMOUS REGION IN MUSLIM MINDANAO"
    r"|REHIYONG AWTONOMO NG MUSLIM MINDANAO)",
    re.IGNORECASE,
)
REGION_TRANSLATION = "(Autonomous Region in Muslim Mindanao)"
PROVINCE = re.compile(
    r"(.+?)(?: PROVINCE)? \(\d+ (?:Municipality|Municipalities|Municipalties)\)",
    re.IGNORECASE,
)
SPECIAL_AREA = re.compile(r"Special Geographic Area \(\d+ Municipalities\)", re.IGNORECASE)
OIC_HEADING = "(OIC Mayors and Vice Mayors)"
ROLE_NOTE = re.compile(r"\((?:ACTING|OIC|OFFICER.IN.CHARGE)\b[^()]*\)", re.IGNORECASE)
CITY_PROVINCE = re.compile(r"\s*\(([^()]+)\)\s*$")
LAYOUTS = {
    "municipal": TableLayout(
        "MUNICIPAL GOVERNMENTS", "MUNICIPALITY ZIP CODE MUNICIPAL MAYOR MUNICIPAL VICE MAYOR",
        "municipality", "zip_code", 0, ("Municipal Mayor", "Municipal Vice Mayor"),
        detail_pattern=r"(?:\d{4}|-{2,})", detail_heading=PROVINCE,
        header_pattern=re.compile(
            r"MUNICIPALITY ZIP CODE (?:MUNICIPAL )?MAYORS? (?:MUNICIPAL )?VICE MAYORS?",
            re.IGNORECASE,
        ),
    ),
    "provincial": TableLayout(
        "PROVINCIAL GOVERNMENTS", "PROVINCE PROVINCIAL GOVERNOR VICE GOVERNOR",
        "province", "capital", 0, ("Governor", "Vice Governor"), header_tail="CAPITAL",
    ),
    "city": TableLayout(
        "CITY GOVERNMENTS", "REGION/CITY CITY MAYOR CITY VICE MAYOR",
        "city", "province", 1, ("City Mayor", "City Vice Mayor"),
        detail_required=False, header_tail="PROVINCE",
        header_pattern=re.compile(r"REGION(?:/ ?| )CITY CITY MAYOR CITY VICE MAYOR"),
    ),
}




def append_continuation(row, cells, location, layout):
    if row is None or row["source_page"] != location["source_page"]:
        return False
    offset = location["top"] - row["last_top"]
    if not 0 < offset <= 12:
        return False
    values = list(map(line_text, cells))
    if layout.detail_pattern and values[1]:
        return False
    mayor, vice_mayor = values[2:]
    email_tails = {}
    for index, value in enumerate((mayor, vice_mayor)):
        contacts = row["contact_lines"][index]
        if value and contacts and EMAIL_START.fullmatch(contacts[-1]["text"]):
            joined = contacts[-1]["text"] + "".join(value.split())
            if EMAIL.fullmatch(joined):
                email_tails[index] = joined
                continue
        if value and not is_contact(value):
            name = row["names"][index]
            if (
                not name or name.upper() in VACANCIES
                or (not ROLE_NOTE.fullmatch(value) and (contacts or not value.isupper()))
            ):
                return False

    for index, field in ((layout.place_column, layout.place), (1 - layout.place_column, layout.detail)):
        if values[index]:
            if not row[field]:
                row[field] = values[index]
            else:
                row[field] += "\n" + values[index]
    for index, value in enumerate((mayor, vice_mayor)):
        if index in email_tails:
            row["contact_lines"][index].append({**location, "text": value, "email": email_tails[index]})
        elif value and is_contact(value):
            contact = {**location, "text": value}
            joined = "".join(value.split())
            if joined != value and EMAIL.fullmatch(joined):
                contact["email"] = joined
            row["contact_lines"][index].append(contact)
        elif value:
            row["names"][index] += "\n" + value
    row["last_top"] = location["top"]
    return True


def page_lines(page):
    # Exclude off-page print content. It can include the facing page's number.
    words = page.extract_words()
    visible = [word for word in words if word["x0"] < page.width and word["x1"] > 0]
    return [
        sorted(line, key=lambda word: word["x0"])
        for line in cluster_objects(visible, "top", tolerance=2)
    ]


def column_starts(line):
    # The first row after the headings must have four well-separated cells.
    # Learn the left edges from the data because some headings are centered.
    starts = [line[0]["x0"]]
    for previous, word in zip(line, line[1:]):
        if word["x0"] - previous["x1"] >= 12:
            starts.append(word["x0"])
    return starts


def recover_header(pdf_path, number, lines, layout):
    """Read a damaged header through PDFium and locate it on the native page."""
    import pypdfium2

    with pypdfium2.PdfDocument(pdf_path) as document:
        page = document[number - 1]
        try:
            text_page = page.get_textpage()
            try:
                text = text_page.get_text_bounded()
            finally:
                text_page.close()
        finally:
            page.close()
    expected = " ".join(filter(None, (layout.header, layout.header_tail)))
    if expected not in " ".join(text.split()):
        return None
    # The secondary reader validates the labels. Keep native coordinates and
    # only replace the header between the title and the first region heading.
    for index, line in enumerate(lines[1:], start=1):
        if REGION.fullmatch(line_text(line)) and index > 1:
            return [lines[0], lines[1], *lines[index:]]
    return None


def continuation_columns(lines, columns):
    """Detect sustained shifts in aligned cells before reading a continuation."""
    starts = [column_starts(line) for line in lines]
    candidates = Counter(
        tuple(round(value) for value in row)
        for row in starts
        if len(row) == 4 and len(columns) == 4
        and all(abs(value - old) <= 18 for value, old in zip(row, columns))
    )
    if not candidates:
        return columns
    candidate, count = candidates.most_common(1)[0]
    if count < 2:
        return columns
    return [old if abs(old - new) <= 3 else new for old, new in zip(columns, candidate)]


def read(pdf_path, first_page, last_page, table="municipal"):
    layout = LAYOUTS[table]
    pages = list(range(first_page, last_page + 1))
    result = {"source": str(pdf_path), "pages": [], "records": [], "vacancies": [], "unlisted": [], "issues": []}
    rows = []
    region = province = columns = current = city_start = city_group_edge = None
    region_website = None
    header_line = None
    administrative_area = role_heading = None
    header_seen = False
    with pdfplumber.open(pdf_path, pages=pages) as pdf:
        if len(pdf.pages) != len(pages):
            raise ValueError("Requested pages exceed the PDF length")
        for page in pdf.pages:
            number = page.page_number
            page_rows = 0
            lines = page_lines(page)
            page.close()
            recovered = (
                recover_header(pdf_path, number, lines, layout)
                if number == first_page and layout.header_tail and lines
                and line_text(lines[0]) == layout.title
                and not any(layout.matches_header(line_text(line)) for line in lines)
                else None
            )
            if recovered:
                lines = recovered
            if columns and table == "provincial":
                columns = continuation_columns(lines, columns)
            for index, line in enumerate(lines):
                printed = line_text(line)
                location = {"source_page": number, "top": round(line[0]["top"], 2)}
                if "\ufffd" in printed or "(cid:" in printed:
                    result["issues"].append({**location, "text": printed, "reason": "Undecoded source text"})
                if layout.matches_header(printed) or (recovered and index == 1):
                    header_seen = True
                    header_line = line
                    columns = current = None
                    continue
                if printed in (layout.title, layout.header_tail) or (table == "provincial" and printed == "CAPITOL"):
                    current = None
                    continue
                if re.fullmatch(r"\d+", printed) and location["top"] >= page.height * 0.95:
                    continue
                if header_seen and layout.detail_heading:
                    if SPECIAL_AREA.fullmatch(printed):
                        administrative_area, province, role_heading, current = printed, None, None, None
                        continue
                    if administrative_area and printed == OIC_HEADING:
                        role_heading, current = printed, None
                        continue
                    if match := layout.detail_heading.fullmatch(printed):
                        province, administrative_area, role_heading, current = match[1], None, None, None
                        continue
                if (header_seen or layout.detail_heading) and REGION.fullmatch(printed):
                    region, province, current = printed, None, None
                    region_website = None
                    administrative_area = role_heading = None
                    if columns and table == "provincial":
                        region_lines = []
                        for entry in lines[index + 1:]:
                            if REGION.fullmatch(line_text(entry)):
                                break
                            region_lines.append(entry)
                        columns = continuation_columns(region_lines, columns)
                    continue
                if table == "provincial" and region and not current and re.fullmatch(r"\(www\.[^\s()]+\)", printed):
                    region_website = printed
                    continue
                if region and not current and printed == REGION_TRANSLATION:
                    region += "\n" + printed
                    continue
                if not header_seen:
                    result["issues"].append({**location, "text": printed, "reason": "Before table header"})
                    continue
                if columns is None:
                    starts = column_starts(line)
                    if layout.place_column == 1 and len(starts) == 3:
                        city_start = starts[0]
                        # The province is empty in the opening city rows.
                        # Learn the provincial edge from the column heading.
                        label = next((entry for entry in lines if line_text(entry) == layout.header_tail), None)
                        if label or header_line:
                            first_label = (label or header_line)[0]
                            starts.insert(0, min(first_label["x0"], city_start))
                    if len(starts) != 4:
                        result["issues"].append({
                            **location, "text": printed, "reason": "Could not establish four columns",
                        })
                        continue
                    columns = starts
                if layout.place_column == 1 and city_start is not None and city_group_edge is None:
                    # Find the recurring city start to the right of the
                    # standalone-city indent, using lines with officials.
                    left_positions = Counter(
                        round(word["x0"])
                        for entry in lines
                        if any(abs(word["x0"] - columns[2]) <= 3 for word in entry)
                        for word in entry
                        if city_start + 10 <= word["x0"] <= city_start + 25
                    )
                    if left_positions:
                        edge, appearances = left_positions.most_common(1)[0]
                        if appearances >= 2:
                            city_group_edge = edge
                # Allow small position drift. Reject text crossing into the next
                # column instead of quietly moving part of a name.
                boundaries = [start - 3 for start in columns[1:]]
                if layout.place_column == 1 and city_start is not None:
                    # Standalone cities use the inner left margin. When a
                    # province is printed, the city column moves right.
                    if line[0]["x0"] < city_start - 3 and any(
                        abs(word["x0"] - city_start) <= 3 for word in line[1:]
                    ):
                        # Some grouped rows use the standalone city indent.
                        boundaries[0] = city_start - 3
                    elif city_group_edge and abs(line[0]["x0"] - city_start) > 3:
                        boundaries[0] = city_group_edge - 2
                cells = split_row_at_columns(line, boundaries)
                crosses_boundary = False
                for column, cell in enumerate(cells[:-1]):
                    if not cell:
                        continue
                    if cells[column + 1]:
                        crosses_boundary |= cell[-1]["x1"] > cells[column + 1][0]["x0"]
                    elif layout.detail_pattern and column < 2:
                        crosses_boundary |= cell[-1]["x1"] > columns[column + 1]
                values = list(map(line_text, cells))
                place, detail = values[layout.place_column], values[1 - layout.place_column]
                mayor, vice_mayor = values[2:]
                has_detail = bool(detail and layout.detail_pattern and re.fullmatch(layout.detail_pattern, detail))
                aligned_roles = all(
                    not cells[i] or abs(cells[i][0]["x0"] - columns[i]) <= 3
                    for i in (2, 3)
                )
                if has_detail and mayor and vice_mayor:
                    # The last role has occasionally been indented within its
                    # column. The ZIP and aligned first role anchor the row.
                    aligned_roles |= (
                        abs(cells[2][0]["x0"] - columns[2]) <= 3
                        and cells[3][0]["x0"] >= columns[3]
                        and cells[3][0]["x0"] - cells[2][-1]["x1"] >= 8
                    )
                missing_detail = (
                    # Without a ZIP anchor, both role cells must establish a
                    # new row. A place and one name can wrap on the same line.
                    layout.detail_pattern and not detail and place and mayor and vice_mayor
                    and abs(cells[layout.place_column][0]["x0"] - columns[layout.place_column]) <= 3
                )
                # A long contact can extend into an empty neighboring column.
                # Its start still identifies the original role.
                contact_overflow = (
                    not place and not detail and mayor and not vice_mayor
                    and len(cells[2]) == 1 and is_contact(mayor)
                )
                if (
                    not crosses_boundary
                    and place
                    and (detail or not layout.detail_required or missing_detail)
                    and (not layout.detail_pattern or has_detail or missing_detail)
                    and (mayor or vice_mayor or has_detail)
                    and aligned_roles
                    and all(not value or not is_contact(value) for value in (mayor, vice_mayor))
                ):
                    if layout.place_column == 1:
                        if detail:
                            province = detail
                        elif city_start is not None and abs(cells[layout.place_column][0]["x0"] - city_start) <= 3:
                            province = None
                    current = {
                        **location,
                        layout.place: place,
                        layout.detail: province if layout.place_column == 1 else detail,
                        "region": region,
                        "names": [mayor, vice_mayor],
                        "contact_lines": [[], []],
                        "last_top": location["top"],
                    }
                    if layout.detail_heading:
                        current["province"] = province
                    if administrative_area:
                        current["administrative_area"] = administrative_area
                    if region_website:
                        current["region_website"] = region_website
                    if role_heading:
                        current["printed_role_heading"] = role_heading
                    rows.append(current)
                    page_rows += 1
                elif (
                    (crosses_boundary and not contact_overflow)
                    or not append_continuation(current, cells, location, layout)
                ):
                    result["issues"].append({**location, "text": printed, "reason": "Unresolved table line"})
                    current = None
                elif layout.place_column == 1:
                    province = current[layout.detail]
            result["pages"].append({"source_page": number, f"{layout.place}_rows": page_rows})
            if not page_rows:
                result["issues"].append({"source_page": number, "reason": f"No {layout.place} rows identified"})

    for row in rows:
        row.pop("last_top")
        if table == "city" and not row["province"] and (match := CITY_PROVINCE.search(row["city"])):
            row["city"] = row["city"][:match.start()]
            row["province"] = match[1]
        names = row.pop("names")
        contacts = row.pop("contact_lines")
        for name, role, lines in zip(names, layout.roles, contacts):
            record = {**row, "role": role, "printed_name": name, "contact_lines": lines}
            if name.upper() in VACANCIES:
                result["vacancies"].append(record)
            elif name:
                result["records"].append(record)
            else:
                result["unlisted"].append(record)
            for index, contact in enumerate(lines):
                if EMAIL_START.fullmatch(contact["text"]) and (
                    index + 1 == len(lines) or "email" not in lines[index + 1]
                ):
                    result["issues"].append({
                        "source_page": contact["source_page"], "top": contact["top"],
                        "text": contact["text"], "reason": "Incomplete email",
                    })
    return result


def find_table_pages(pdf_path, table="municipal"):
    """Return the inclusive, one-based table span from body-page evidence."""
    import pypdfium2

    municipal = LAYOUTS["municipal"]
    municipal_title = re.compile(rf"^(?:\d+ )?{municipal.title}(?: |$)", re.IGNORECASE)
    provincial_header = r"PROVINCE PROVINCIAL GOVERNOR VICE GOVERNOR CAPIT[AO]L"
    city_header = (
        r"REGION(?:/ ?| )CITY (?:CITY MAYOR CITY VICE MAYOR(?: PROVINCE)?|PROVINCE CITY MAYOR CITY VICE MAYOR)"
    )
    body_headers = {
        "provincial": re.compile(rf"^(?:\d+ )?(?:PROVINCIAL GOVERNMENTS )?{provincial_header}\b"),
        "city": re.compile(rf"^(?:\d+ )?(?:CITY GOVERNMENTS )?{city_header}\b"),
    }
    regional_leader = re.compile(r"^(?:\d+ )?[^\d]{1,100}?(?:Regional Governor|Chief Minister)\b", re.IGNORECASE)
    second_role = re.compile(r"Regional Vice Governor|Deputy Chief Minister", re.IGNORECASE)
    first_page = None
    last_body_page = None

    def starts_table(kind, text, number):
        if kind in body_headers:
            return body_headers[kind].match(text) is not None
        if not municipal_title.match(text):
            return False
        # PDFium can join the last header cell to the first municipality.
        with pdfplumber.open(pdf_path, pages=[number]) as pdf:
            return any(municipal.matches_header(line_text(line)) for line in page_lines(pdf.pages[0]))

    with pypdfium2.PdfDocument(pdf_path) as document:
        for index in range(len(document)):
            page = document[index]
            try:
                text_page = page.get_textpage()
                try:
                    text = " ".join(text_page.get_text_bounded().split())
                finally:
                    text_page.close()
            finally:
                page.close()
            number = index + 1
            if first_page is None:
                if starts_table(table, text, number):
                    first_page = last_body_page = number
                continue
            if table == "municipal":
                if regional_leader.match(text) and second_role.search(text[:1000]):
                    return first_page, index
            else:
                next_table = "city" if table == "provincial" else "municipal"
                if starts_table(next_table, text, number):
                    return first_page, last_body_page
                if text and not re.match(r"^LOCAL GOVERNMENT UNITS\b", text, re.IGNORECASE):
                    last_body_page = number
    missing = "end" if first_page is not None else "start"
    raise ValueError(f"Could not locate the {table} table {missing} in {pdf_path}. Supply both PDF page numbers.")
