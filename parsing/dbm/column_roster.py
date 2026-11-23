"""Read positioned rosters with location, official and contact columns."""

from collections import Counter

from .layout import bold, line_text, source_line
from .common import VACANCIES


def locate_roster_columns(rows):
    """Infer the two interior starts from recurring aligned name and contact words."""
    names = Counter()
    for _, words in rows:
        if not bold(words[0]):
            name = next((word for word in words[1:] if bold(word)), None)
            if name:
                names[round(name["x0"])] += 1
    if not names:
        return None
    name_x, name_count = names.most_common(1)[0]
    contacts = Counter(round(word["x0"]) for _, words in rows for word in words
                       if word["x0"] >= name_x + 100)
    if not contacts or name_count < 2:
        return None
    contact_x, contact_count = contacts.most_common(1)[0]
    if contact_count < 2:
        return None
    return name_x, contact_x


def read_location_official_contact_columns(rows, column_starts=None):
    """Group positioned rows into postings, preserving the source in each column.

    The middle column starts a posting with a bold name or printed vacancy.
    A centered bold row inside that column can introduce a location group.
    Supply (official_x, contact_x) for layouts with known column starts.
    """
    result = {"group_headings": [], "records": [], "issues": []}
    starts = column_starts if column_starts is not None else locate_roster_columns(rows)
    if starts is None:
        result["issues"].extend({**source_line(words, page), "reason": "Unclear roster columns"}
                                for page, words in rows)
        if not rows:
            result["issues"].append({"reason": "No positioned roster rows"})
        return result
    name_x, contact_x = starts
    result["column_starts"] = {"official": name_x, "contact": contact_x}

    row_positions = [(page, min(word["top"] for word in words)) for page, words in rows]
    spacing = Counter(
        round(top - previous_top, 1)
        for (previous_page, previous_top), (page, top) in zip(row_positions, row_positions[1:])
        if previous_page == page and top > previous_top
    )
    line_step = spacing.most_common(1)[0][0] if spacing else None

    record = group = None
    for index, (page, words) in enumerate(rows):
        left = [word for word in words if word["x0"] < name_x - 1.5]
        middle = [word for word in words if name_x - 1.5 <= word["x0"] < contact_x - 1.5]
        right = [word for word in words if word["x0"] >= contact_x - 1.5]
        value = line_text(words)
        if (not left and not right and words[0]["x0"] > name_x + 8
                and all(map(bold, words)) and value.upper() not in VACANCIES):
            group = source_line(words, page)
            result["group_headings"].append(group)
            record = None
            continue
        title = line_text(middle)
        vacant = title.upper() in VACANCIES
        name_end = next((index for index, word in enumerate(middle) if not bold(word)), len(middle))
        if name_end or vacant:
            name = source_line(middle[:name_end] if name_end else middle, page)
            # Closely spaced bold lines before the role can be one wrapped name.
            continues_name = (
                record is not None and not record["vacant"] and not vacant
                and not record["role_lines"] and line_step is not None and index > 0
                and abs(name["x"] - record["name_lines"][0]["x"]) <= 2
                and record["name_lines"][-1]["source_page"] == page == row_positions[index - 1][0]
                and abs(record["name_lines"][-1]["top"] - row_positions[index - 1][1]) <= 2
                and abs(row_positions[index][1] - row_positions[index - 1][1] - line_step) <= 2
            )
            if continues_name:
                record["name_lines"].append(name)
                record["printed_name"] += " " + name["text"]
            else:
                record = {
                    "printed_name": name["text"], "name_lines": [name],
                    "group_source": group, "vacant": vacant,
                    "location_lines": [], "role_lines": [], "contact_lines": [],
                }
                result["records"].append(record)
            middle = middle[name_end:] if name_end else []
        if record is None:
            result["issues"].append({**source_line(words, page), "reason": "Row before a posting"})
            continue
        for field, parts in (("location_lines", left), ("role_lines", middle), ("contact_lines", right)):
            if parts:
                record[field].append(source_line(parts, page))

    if not result["records"]:
        result["issues"].append({"reason": "No roster postings found"})
    for record in result["records"]:
        name_source = record["name_lines"][0]
        if not record["location_lines"]:
            result["issues"].append({**name_source, "reason": "Posting has no printed location"})
        if not record["role_lines"] and not record["vacant"]:
            result["issues"].append({**name_source, "reason": "Posting has no printed role"})
    return result
