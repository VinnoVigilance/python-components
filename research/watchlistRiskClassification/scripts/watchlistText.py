"""Render a canonical watchlist record to the labelling text (same rendering as the 900-record sample)."""

NOISE = {"Language Spoken", "Height", "Height (in)", "Weight", "Hair", "Eyes", "Distinguishing Marks", "Scars and Marks",
         "Race", "Ethnic Origin", "Complexion", "Build", "Age Range", "Reporting Phone", "National Reference", "Warrant Case Number"}


def values(items, keys):
    """Join the given keys of each dict item with ' ; ', skipping empty items."""
    out = []
    for x in items or []:
        if isinstance(x, dict):
            v = " ; ".join(str(x.get(k, "")).strip() for k in keys if str(x.get(k, "")).strip())
            if v:
                out.append(v)
    return out


def render(record, list_name):
    """Build the labelling text for one canonical record."""
    first_source = (record.get("Sources") or [{}])[0]

    def all_keys(field):
        items = record.get(field) or []
        return list(items[0].keys()) if items else []

    lines = [f"Source list: {list_name}", f"Entity type: {record.get('EntityType', '')}"]
    sections = [
        ("Programs", values(record.get("Programs"), ["ProgramType", "Program", "Authority", "Note"])),
        ("Measures", values(record.get("Measures"), all_keys("Measures"))),
        ("Titles", values(record.get("Titles"), ["Title", "Note"])),
        ("Occupations", values(record.get("Occupations"), all_keys("Occupations"))),
        ("Relationships", values(record.get("Relationships"), all_keys("Relationships"))),
        ("Additional info", values([a for a in record.get("additionalInfo") or [] if a.get("Type") not in NOISE], ["Type", "Value"])),
        ("Source note", [first_source.get("Note", "").strip()] if first_source.get("Note", "").strip() else []),
        ("Remarks", values(record.get("Comments"), ["type", "text"])),
    ]
    for name, items in sections:
        unique = list(dict.fromkeys(items))
        if unique:
            lines.append(f"{name}: " + " || ".join(unique))
    return "\n".join(lines)
