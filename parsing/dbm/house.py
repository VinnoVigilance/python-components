"""Read DBM House district, party-list, and committee listings."""

import re
from collections import Counter

import pdfplumber
import pypdfium2

from .layout import bold, line_text, positioned_lines, source_line, split_row_at_columns
from .common import VACANCIES


HOUSE = "HOUSE LEADERS"
MEMBERS = "HOUSE MEMBERS"
PARTY_LIST = "PARTY-LIST REPRESENTATIVES"
COMMITTEES = "HOUSE COMMITTEES"
SPECIAL = "SPECIAL COMMITTEES"
SECRETARIAT = "HOUSE OF REPRESENTATIVES SECRETARIAT OFFICIALS"
PHONE = re.compile(r"\d{3,4}[-–]\d{4}[;/,]?")
EXTENSION = re.compile(r"\d{4}(?:\s*/\s*\d{4})?")
PLACEHOLDERS = {"-", "–", "—"}
DROPPED = re.compile(r"\s*(\(Dropped from the Roll\))$", re.IGNORECASE)
DISTRICT = re.compile(r"(?:Lone|\d+(?:st|nd|rd|th)?)", re.IGNORECASE)
AREA_CODE = re.compile(r"\(\d+\)")
QUALIFIER = re.compile(
    r"\s*(\((?:(?:Legislative )?Caretaker|Legislative Liaison Offi\s*cer|Deceased)\))$", re.IGNORECASE,
)
HEADERS = {"District", "Direct", "Local", "Email"}


def find_house_boundaries(pdf_path):
    house = members = party_list = None
    with pypdfium2.PdfDocument(pdf_path) as pdf:
        for index in range(len(pdf)):
            page = pdf[index]
            text_page = page.get_textpage()
            try:
                headings = {" ".join(line.upper().split()) for line in text_page.get_text_range().splitlines()}
            finally:
                text_page.close()
                page.close()
            if house is None and HOUSE in headings:
                house = index + 1
            if house is not None and MEMBERS in headings and members is None:
                members = index + 1
            if house is not None and PARTY_LIST in headings and party_list is None:
                party_list = index + 1
            if members is not None and party_list is not None and SECRETARIAT in headings:
                return house, members, party_list, index + 1
    raise ValueError("Could not locate the House party-list and committee sections")


def read_sections(lines, result):
    section = None
    committee = None
    party_rows = []
    headings = result["headings"]
    for page, words in lines:
        text = line_text(words)
        if text == SECRETARIAT:
            headings.append(source_line(words, page))
            break
        if text in {PARTY_LIST, COMMITTEES, SPECIAL}:
            section = text
            headings.append(source_line(words, page))
            committee = None
            continue
        if section == PARTY_LIST:
            party_rows.append((page, words))
        elif section in {COMMITTEES, SPECIAL}:
            if text.casefold() in {"chairperson", "chairpersons"}:
                headings.append(source_line(words, page))
                continue
            committee = read_committee(page, words, section, committee, result)
    found = {heading["text"] for heading in headings}
    if not {PARTY_LIST, COMMITTEES, SPECIAL, SECRETARIAT} <= found:
        result["issues"].append({"reason": "Missing House section heading", "observed": sorted(found)})
    return party_rows


def read_committee(page, words, section, previous, result):
    source = source_line(words, page)
    if words[-1]["text"] in PLACEHOLDERS:
        name_start = len(words) - 1
    else:
        name_start = next((i for i, word in enumerate(words) if bold(word)), len(words))
    left, right = words[:name_start], words[name_start:]
    if not left:
        result["issues"].append({**source, "reason": "Committee row has no label"})
        return previous
    if not right and previous is not None:
        last = previous["committee_lines"][-1]
        if last["source_page"] == page and source["top"] - last["top"] <= 12:
            previous["committee_lines"].append(source)
            return previous

    occupant = None
    name = None
    qualifier = None
    if right:
        occupant = source_line(right, page)
        occupant_text = occupant["text"]
        if occupant_text.upper() in VACANCIES:
            status = "vacant"
        elif occupant_text in PLACEHOLDERS:
            status = "placeholder"
        else:
            status = "named"
            name, separator, suffix = occupant_text.partition(" (")
            if separator:
                qualifier = "(" + suffix
    else:
        status = "unlisted"
    record = {
        "section": section, "committee_lines": [source_line(left, page)],
        "printed_name": name, "qualifier": qualifier, "occupant_source": occupant,
        "role": "Chairperson", "status": status,
    }
    result["committees"].append(record)
    return record


def modal_x(words, label):
    positions = Counter(round(word["x0"]) for word in words)
    if not positions:
        raise ValueError(f"Could not locate the House table's {label} column")
    return positions.most_common(1)[0][0]


def party_columns(rows):
    lines = [line for _, line in rows]
    margin = modal_x((line[0] for line in lines), "left")
    member = modal_x((word for line in lines for word in line if word["text"].endswith(",")), "representative")
    extension = modal_x((line[-1] for line in lines if EXTENSION.fullmatch(line[-1]["text"])), "extension")
    phone = modal_x(
        (line[index - 1] if index and re.fullmatch(r"\(\d+\)", line[index - 1]["text"]) else word
         for line in lines for index, word in enumerate(line) if PHONE.fullmatch(word["text"])), "phone",
    )
    member_first = abs(member - margin) <= 3
    if member_first:
        inner = modal_x(
            (word for line in lines if abs(line[0]["x0"] - member) <= 3
             for i, word in enumerate(line[1:], 1)
             if member < word["x0"] < phone - 3 and word["x0"] - line[i - 1]["x1"] >= 8), "party",
        )
    else:
        inner = member
    if not margin < inner < phone < extension:
        raise ValueError("Could not establish the House party-list column order")
    return member_first, (inner - 2, phone - 3, extension - 2)


def row_cells(words, starts, first_field):
    # At identical starts, the first declared field owns the words.
    ordered = sorted(reversed(starts), key=starts.__getitem__)
    columns = split_row_at_columns(words, [starts[field] for field in ordered])
    fields = dict(zip([first_field, *ordered], columns, strict=True))
    return {field: fields[field] for field in [first_field, *starts]}


def read_party_lists(rows, result):
    member_first, (inner, phone, extension) = party_columns(rows)
    starts = {"right": inner, "phone": phone, "extension": extension}
    name_field, label_field = ("left", "right") if member_first else ("right", "left")
    party = current = last_source = None
    for page, words in rows:
        source = source_line(words, page)
        cells = row_cells(words, starts, "left")
        name, label = cells[name_field], cells[label_field]
        contacts, extensions = cells["phone"], cells["extension"]
        close = last_source and last_source["source_page"] == page and source["top"] - last_source["top"] <= 12
        last_source = source

        begins_name = any(word["text"].endswith(",") for word in name) or line_text(name).upper() in VACANCIES
        if label and (begins_name or not close or party is None or not member_first):
            party = {"label_lines": [source_line(label, page)], "records": [], "unassigned_contacts": []}
            result["parties"].append(party)
            current = None
        elif label:
            party["label_lines"].append(source_line(label, page))
        if party is None:
            result["issues"].append({**source, "reason": "Party-list row has no party context"})
            continue
        if begins_name or (name and (current is None or not close)):
            current = {
                "role": "Party-list Representative", "name_lines": [source_line(name, page)],
                "phone_lines": [], "extension_lines": [],
            }
            party["records"].append(current)
        elif name:
            current["name_lines"].append(source_line(name, page))
        if not (contacts or extensions):
            continue
        continues_phone = (
            current and close and contacts and not extensions and current["phone_lines"]
            and current["phone_lines"][-1]["text"].endswith("/")
        )
        continues_single_listing = (
            current and close and not name and not label
            and contacts and extensions and len(party["records"]) == 1
        )
        if name or continues_phone or continues_single_listing:
            if contacts:
                current["phone_lines"].append(source_line(contacts, page))
            if extensions:
                current["extension_lines"].append(source_line(extensions, page))
        else:
            party["unassigned_contacts"].append({
                "source": source,
                "phone": source_line(contacts, page) if contacts else None,
                "extension": source_line(extensions, page) if extensions else None,
            })
    for party in result["parties"]:
        party["printed_label"] = " ".join(line["text"] for line in party["label_lines"])
        for record in party["records"]:
            name = " ".join(line["text"] for line in record["name_lines"])
            qualifier = DROPPED.search(name)
            record["qualifier"] = qualifier[1] if qualifier else None
            record["printed_name"] = name[:qualifier.start()] if qualifier else name
            record["vacant"] = record["printed_name"].upper() in VACANCIES


def read_party_lists_and_committees(pdf_path):
    _, _, first, last = find_house_boundaries(pdf_path)
    result = {
        "source": str(pdf_path), "pages": [first, last], "headings": [],
        "parties": [], "committees": [], "issues": [],
    }
    lines = []
    with pdfplumber.open(pdf_path, pages=list(range(first, last + 1))) as pdf:
        for page in pdf.pages:
            lines.extend((page.page_number, words) for words in positioned_lines(page) if not line_text(words).isdigit())
            page.close()
    read_party_lists(read_sections(lines, result), result)
    for record in result["committees"]:
        record["printed_committee"] = " ".join(line["text"] for line in record["committee_lines"])
    return result


def read_district_rows(pdf_path, first, last):
    header = None
    rows = []
    with pdfplumber.open(pdf_path, pages=list(range(first, last + 1))) as pdf:
        for page in pdf.pages:
            lines = positioned_lines(page)
            page.close()
            for words in lines:
                text = line_text(words)
                if text == PARTY_LIST:
                    return header, rows
                if HEADERS - {"Email"} <= {word["text"] for word in words} and "Province/City" in text:
                    header = words
                elif header is not None and text != MEMBERS:
                    rows.append((page.page_number, words))
    raise ValueError("Could not locate the party-list boundary in the positioned House pages")


def district_column_starts(header, rows):
    labels = {word["text"]: word["x0"] for word in header}
    candidates = {
        "district": (labels["District"], 22, lambda word: DISTRICT.fullmatch(word["text"])),
        "phone": (labels["Direct"], 22, lambda word: PHONE.fullmatch(word["text"]) or AREA_CODE.fullmatch(word["text"])),
        "extension": (labels["Local"], 15,
                      lambda word: EXTENSION.fullmatch(word["text"].rstrip(",;/"))),
    }
    if "Email" in labels:
        candidates["email"] = (labels["Email"], 40, lambda word: "@" in word["text"])
    starts = {}
    for field, (center, tolerance, matches) in candidates.items():
        choices = [word for _, words in rows for word in words if abs(word["x0"] - center) < tolerance and matches(word)]
        starts[field] = modal_x(choices, field)
        if field in {"district", "phone"}:
            # Lone can extend to the left of ordinal districts. An area code
            # can occupy the phone column's left edge before the local number.
            nearby = [word["x0"] for word in choices if abs(word["x0"] - starts[field]) <= 8]
            starts[field] = min(nearby)
    if any(word["text"] == "Province/City" for word in header):
        center = labels["Province/City"]
        starts["place"] = modal_x(
            (word for _, words in rows for index, word in enumerate(words[1:], 1)
             if abs(word["x0"] - center) < 27 and word["x0"] - words[index - 1]["x1"] >= 6), "place",
        )
    area_codes = [word["x0"] for _, words in rows for word in words
                  if abs(word["x0"] - labels["Direct"]) < 10 and AREA_CODE.fullmatch(word["text"])]
    starts["phone"] = min([starts["phone"], *area_codes])
    return {field: x - 2 for field, x in starts.items()}


def read_districts(pdf_path):
    _, first, last, _ = find_house_boundaries(pdf_path)
    if first is None:
        raise ValueError("Could not locate the House Members heading")
    header, rows = read_district_rows(pdf_path, first, last)
    if header is None:
        raise ValueError("Could not locate the House district table header")
    starts = district_column_starts(header, rows)
    inline_place = "place" in starts
    result = {"source": str(pdf_path), "pages": [first, rows[-1][0]], "places": [], "issues": []}
    place = record = previous = None
    for page, words in rows:
        cells = row_cells(words, starts, "name")
        parts = {field: source_line(cell, page) for field, cell in cells.items() if cell}
        source = source_line(words, page)
        label = parts.get("place")
        district = parts.get("district")
        begins_row = district and DISTRICT.fullmatch(cells["district"][0]["text"])
        if not inline_place and set(parts) == {"name"} and all(map(bold, words)):
            label = parts.pop("name")
        if label and (not inline_place or begins_row):
            place = {"printed_place": label["text"], "place_lines": [label], "records": []}
            result["places"].append(place)
            record = None
        if begins_row and place is not None:
            record = {"role": "District Representative", "fields": {}}
            place["records"].append(record)
        elif label and not inline_place:
            previous = source
            continue
        elif not (
            record and previous and previous["source_page"] == page
            and source["top"] - previous["top"] <= 12
        ):
            result["issues"].append({**source, "reason": "District line without a clear owner"})
            previous = source
            continue
        previous = source
        for field, part in parts.items():
            if field == "place":
                if not begins_row:
                    place["place_lines"].append(part)
                continue
            record["fields"].setdefault(field, []).append(part)

    for place in result["places"]:
        place["printed_place"] = " ".join(part["text"] for part in place["place_lines"])
        for record in place["records"]:
            fields = record["fields"]
            name = " ".join(part["text"] for part in fields.get("name", []))
            qualifier = QUALIFIER.search(name)
            record["printed_name"] = (name[:qualifier.start()] if qualifier else name) or None
            record["qualifier"] = qualifier[1] if qualifier else None
            record["printed_district"] = " ".join(part["text"] for part in fields["district"])
            record["vacant"] = name.upper() in VACANCIES
    return result
