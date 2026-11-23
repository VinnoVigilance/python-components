import hashlib
import json
import os
import re
import unicodedata
from copy import deepcopy
from string import Formatter
from urllib.parse import unquote, urlparse

from nameparser import HumanName


# "NA" is intentionally excluded: it is Namibia's ISO country code, not a
# "nothing here" marker. Sources that mean empty use "N/A".
EMPTY_VALUES = {"", "N/A", "NONE", "NULL", "UNKNOWN", "-"}


DBM_SECTION_NAMES = {
    "national_government": "National Government",
    "executive_branch": "Executive Branch",
    "legislative_branch": "Legislative Branch",
    "judicial_branch": "Judicial Branch",
    "local_government_units": "Local Government Units",
    "provincial_governments": "Provincial Governments",
    "city_governments": "City Governments",
    "municipal_governments": "Municipal Governments",
    "diplomatic_and_consular_missions": "Diplomatic and Consular Missions",
    "international_organizations": "International Organizations",
    "house_of_representatives": "House of Representatives",
    "senate": "Senate",
}
DBM_EMAIL = re.compile(r"[\w.+%-]+@[\w-]+(?:\.[\w-]+)+")
DBM_WEBSITE = re.compile(
    r"(?:https?://|www\.)[^\s;,]+|"
    r"(?:[\w-]+\.)+[A-Za-z]{2,24}(?:/[^\s;,]*)?",
    re.IGNORECASE,
)
DBM_EXTENSION = re.compile(
    r"\b(?:(?:loc\s*\.?|local|ext\s*\.?)\s*:?\s*)+"
    r"([\d,. /–—-]+(?:to\s*\d+)?)$", re.IGNORECASE,
)
DBM_PHONE = re.compile(r"[\d\s()+./,–—-]+(?:\s+to\s*\d+)?", re.IGNORECASE)
DBM_VANITY_PHONE = re.compile(
    r"(?:\(\+?\d[\d -]*\)\s*)?\d+-[A-Z0-9]*[A-Z][A-Z0-9]*"
    r"(?:\s*\([\d-]+\))?"
)
DBM_ADDRESS = re.compile(
    r"\b(?:St\.?|Street|Ave\.?|Avenue|Road|Blvd\.?|Bldg\.?|Building|"
    r"Levels?|Drive|Parkway|Campus|Sitio|Subdivision)\b", re.IGNORECASE,
)
DBM_POSTAL_ADDRESS = re.compile(r"^\d{4}\s+[^\W\d_]", re.UNICODE)


class PreProcessingEngine:

    def _dbm_text(self, value):
        if isinstance(value, dict):
            return value.get("text") or ""
        return value if isinstance(value, str) else ""


    def _dbm_join(self, lines):
        value = {"lines": lines}
        self.join_object_field_values(value, {
            "source_path": "lines", "value_path": "text",
            "target_field": "text", "separator": " ",
        })
        return value.get("text") or ""


    def _dbm_hierarchy(self, record):
        hierarchy = []

        def add(kind, name):
            if name and not any(item == {"kind": kind, "name": name} for item in hierarchy):
                hierarchy.append({"kind": kind, "name": name})

        path = record["source_path"]
        for part in path:
            if part in DBM_SECTION_NAMES:
                add("branch" if str(part).endswith("_branch") else "section", DBM_SECTION_NAMES[part])

        for entry in record.get("context", []):
            fields = entry["fields"]
            parent = self._dbm_text(fields.get("parent_heading"))
            add("department" if parent.startswith("DEPARTMENT OF ") else "parent", parent)
            heading = self._dbm_text(fields.get("heading"))
            lines = ([{"text": heading}] if heading else fields.get("heading_lines", []))
            heading = self._dbm_join([*lines, *fields.get("heading_continuation_lines", [])])
            context_path = entry["source_path"]
            if heading.startswith("DEPARTMENT OF "):
                kind = "department"
            elif "courts" in context_path and "groups" not in context_path:
                kind = "court"
            elif "colleges" in context_path and "groups" in context_path:
                kind = "region"
            else:
                kind = "group" if "groups" in context_path else "section"
            add(kind, heading)
            for field, kind in (
                ("country", "country"), ("printed_mission", "mission"),
                ("printed_organization", "organization"), ("printed_office", "office"),
                ("printed_place", "jurisdiction"), ("printed_label", "party"),
                ("locality", "locality"),
            ):
                add(kind, self._dbm_text(fields.get(field)))

        for field in ("region", "province", "city", "municipality"):
            add(field, self._dbm_text(record.get(field)))
        add("group", self._dbm_text(record.get("group_source")) or self._dbm_text(record.get("group")))
        add("committee", self._dbm_join(record.get("committee_lines", [])))
        office = self._dbm_text(record.get("office")) or self._dbm_join(record.get("office_lines", []))
        organization_layout = "corporations_and_colleges" in path or "other_executive_offices" in path
        add("organization" if organization_layout else "office", office)
        add("posting", self._dbm_join(record.get("location_lines", [])))
        return hierarchy


    def _dbm_classify_contact(self, text, prepared):
        for original in text.split(";"):
            part = original.strip()
            if "@" in part:
                email = re.sub(r"(?<=\w)\s*@\s*(?=[\w-])", "@", part)
                email = re.sub(r"^e-?mails?\s*:\s*", "", email, flags=re.IGNORECASE)
                emails = [value.strip() for value in email.split(",")]
                if all(DBM_EMAIL.fullmatch(value) for value in emails):
                    for value in emails:
                        if value not in prepared["emails"]:
                            prepared["emails"].append(value)
                else:
                    prepared["contact_unparsed"].append(part)
                continue
            part = re.sub(r"\bwww\s*[.,]\s*(?=[A-Za-z0-9])", "www.", part, flags=re.IGNORECASE)
            for match in DBM_WEBSITE.finditer(part):
                website = match.group().rstrip(".,")
                if website not in prepared["websites"]:
                    prepared["websites"].append(website)
            part = DBM_WEBSITE.sub("", part)
            part = part.strip(" ,")
            if not part:
                continue
            part = re.sub(r"^(?:e-?mail|website)\s*:?\s*$", "", part, flags=re.IGNORECASE)
            if not part:
                continue
            fax = bool(re.search(r"^(?:fax|F)\b|\sF$", part, re.IGNORECASE))
            phone_fax = bool(re.search(r"^(?:telefax|TF)\b|\sTF$", part, re.IGNORECASE))
            part = re.sub(r"^(?:tel(?:ephone)?|telefax|phone|fax|trunk\s*lines?|hotlines?|TL|DL|TF|F)\b\s*(?:No\.?\s*)?[.:]?\s*", "", part, flags=re.IGNORECASE)
            part = re.sub(r"\s+(?:F|TL|DL|TF)$", "", part, flags=re.IGNORECASE)
            extension = DBM_EXTENSION.search(part)
            number = part[:extension.start()].strip() if extension else part
            valid_number = number and (
                DBM_PHONE.fullmatch(number) or DBM_VANITY_PHONE.fullmatch(number)
            ) and any(char.isdigit() for char in number)
            if valid_number or (extension and not number):
                prepared["phones"].append({
                    "number": number or None,
                    "extension": extension[1].strip() if extension else None,
                    "kind": "fax" if fax else "phone_fax" if phone_fax else "phone",
                })
            else:
                prepared["contact_unparsed"].append(part)


    def _dbm_contacts(self, record, prepared):
        fields = record.get("fields") or {}
        phones = record.get("phone_lines", fields.get("phone", []))
        extensions = record.get("extension_lines", fields.get("extension", []))
        used = set()
        for line in phones:
            matches = [
                (index, extension) for index, extension in enumerate(extensions)
                if extension.get("source_page") == line.get("source_page")
                and extension.get("top") == line.get("top")
            ]
            if len(matches) == 1:
                index, extension = matches[0]
                used.add(index)
                prepared["phones"].append({
                    "number": self._dbm_text(line), "extension": self._dbm_text(extension), "kind": "phone",
                })
            else:
                self._dbm_classify_contact(self._dbm_text(line), prepared)
        for index, line in enumerate(extensions):
            if index not in used:
                prepared["phones"].append({"number": None, "extension": self._dbm_text(line), "kind": "phone"})
        lines = []
        addresses = list(record.get("address_lines", []))
        for line in [*record.get("contact_lines", []), *fields.get("email", [])]:
            printed = self._dbm_text(line)
            address = DBM_ADDRESS.search(printed) or (
                record.get("address_lines") and DBM_POSTAL_ADDRESS.match(printed)
            )
            if address and "@" not in printed and not DBM_WEBSITE.search(printed):
                addresses.append(line)
                continue
            compact = "".join(self._dbm_text(line).split())
            text = compact if DBM_EMAIL.fullmatch(compact) else self._dbm_text(line)
            if lines:
                previous, previous_text = lines[-1]
                adjacent = (
                    line.get("source_page") == previous.get("source_page")
                    and 0 < line.get("top", 0) - previous.get("top", 0) <= 15
                )
                fragment = previous_text.rstrip().endswith(("@", ".")) or previous_text.lower() == "www"
                combined = "".join((previous_text + text).split())
                if adjacent and fragment and (
                    DBM_EMAIL.fullmatch(combined)
                    or (combined.lower().startswith(("www.", "http")) and DBM_WEBSITE.fullmatch(combined))
                ):
                    lines[-1] = (line, combined)
                    continue
            lines.append((line, text))
        for _, text in lines:
            self._dbm_classify_contact(text, prepared)
        if addresses:
            addresses.sort(key=lambda line: (line.get("source_page", 0), line.get("top", 0)))
            prepared["address_text"] = self._dbm_join(addresses)


    def _dbm_office_contacts(self, lines, prepared):
        """Institution header lines contain addresses, contacts, and edition captions."""
        office = {"address_lines": [], "contact_lines": []}
        for line in lines:
            text = self._dbm_text(line)
            probe = {"phones": [], "emails": [], "websites": [], "contact_unparsed": []}
            self._dbm_classify_contact(text, probe)
            qualified = re.match(r"^([^:@]+):\s*((?:\(\+?[\d -]+\)|\d).*)$", text)
            if qualified and not probe["phones"]:
                parsed = {"phones": [], "emails": [], "websites": [], "contact_unparsed": []}
                self._dbm_classify_contact(qualified[2], parsed)
                if parsed["phones"] and not parsed["contact_unparsed"]:
                    prepared["phones"].extend({**phone, "label": qualified[1].strip()} for phone in parsed["phones"])
                    continue
            contact = (
                probe["phones"] or probe["emails"] or probe["websites"] or "@" in text
                or re.search(r"^(?:tel|phone|fax|trunk\s*line|hotline|TL\b|DL\b|TF\b|Hours\b)", text, re.IGNORECASE)
                or re.search(r"\bCONGRESS\b", text, re.IGNORECASE)
            )
            office["contact_lines" if contact else "address_lines"].append(line)
        self._dbm_contacts(office, prepared)
        addresses, chunk, label = [], [], ""
        for line in office["address_lines"]:
            text = self._dbm_text(line)
            start = re.match(r"^((?:Main Office|Extension(?: Office)?|[\w ’'-]+ (?:Office|Branch))):\s*(.*)", text, re.IGNORECASE)
            branch = re.match(r"^(?:Consulate|Embassy)\b", text, re.IGNORECASE)
            if start or branch:
                if chunk:
                    addresses.append({"text": self._dbm_join(chunk), "label": label})
                chunk = []
                label = start[1] if start else text
                text = start[2] if start else ""
            if text:
                chunk.append({**line, "text": text})
        if chunk:
            addresses.append({"text": self._dbm_join(chunk), "label": label})
        prepared["addresses"] = addresses

    def _dbm_organization_details(self, record, prepared):
        contexts = []
        for entry in record.get("context", []):
            fields = entry["fields"]
            heading = self._dbm_text(fields.get("heading") or fields.get("printed_mission") or fields.get("printed_organization"))
            lines = [{"text": heading}] if heading else fields.get("heading_lines", [])
            heading = self._dbm_join([*lines, *fields.get("heading_continuation_lines", [])])
            contexts.append((entry, heading))

        if not prepared["organization_name"]:
            for entry, heading in reversed(contexts):
                institutional = (
                    heading.startswith("OFFICE OF ") or heading.endswith(" OFFICE")
                    or "constitutional_offices" in entry["source_path"]
                    or "departments" in entry["source_path"] or "related_rosters" in entry["source_path"]
                    or "bangsamoro" in entry["source_path"]
                    or heading == "SENATE OF THE PHILIPPINES"
                )
                if heading and institutional:
                    prepared["organization_name"] = heading
                    break
        if not prepared["organization_name"]:
            for chamber in ("senate", "house_of_representatives"):
                if chamber in record["source_path"]:
                    prepared["organization_name"] = DBM_SECTION_NAMES[chamber]
                    break

        group = self._dbm_text(record.get("group_source"))
        if not prepared["office_text"] and group.startswith("OFFICE OF "):
            prepared["office_text"] = group

        details = None
        for entry, heading in reversed(contexts):
            fields = entry["fields"]
            header = (fields.get("header_lines") or fields.get("details") or fields.get("regional_details")
                      or fields.get("mission_lines") or fields.get("office_lines") or [])
            organization = str(prepared["organization_name"] or "").casefold()
            header_names_organization = organization and any(self._dbm_text(line).casefold() == organization for line in header)
            if not organization or (heading.casefold() != organization and not header_names_organization):
                continue
            if not header:
                continue
            details = {
                "name": prepared["organization_name"],
                "address_text": None,
                "phones": [], "emails": [], "websites": [], "contact_unparsed": [],
                "source_path": deepcopy(entry["source_path"]),
                "source_pages": sorted({line["source_page"] for line in header if isinstance(line.get("source_page"), int)}),
            }
            self._dbm_office_contacts([line for line in header if self._dbm_text(line).casefold() != organization], details)
            break
        prepared["organization_details"] = details
        self._dbm_apply_office_context(prepared)

    def _dbm_apply_office_context(self, prepared):
        details = prepared.get("organization_details")
        if prepared["address_text"]:
            prepared["addresses"] = [{"text": prepared["address_text"], "note": "Office address printed for this listing"}]
        else:
            prepared["addresses"] = [
                {"text": address["text"], "note": "Shared office address — " + details["name"]
                 + ("; " + address["label"] if address["label"] else "")}
                for address in (details or {}).get("addresses", [])
            ]

        contacts = []
        bases = [phone for phone in (details or {}).get("phones", []) if phone["number"] and phone["kind"] != "fax"]
        for phone in prepared["phones"]:
            number, extension = phone["number"], phone["extension"]
            kind = phone["kind"]
            note = "Office " + {"fax": "fax", "phone_fax": "telephone/fax", "phone": "telephone"}[kind] + " printed for this listing"
            if extension and not number:
                if len(bases) == 1:
                    number = bases[0]["number"]
                    note = "Office number from the shared header; extension printed for this listing"
                else:
                    note = "Office extension; base number not uniquely identified in the directory"
            elif extension:
                note = "Office " + ("telephone/fax" if kind == "phone_fax" else "fax" if kind == "fax" else "telephone") + " and extension printed for this listing"
            value = number or ""
            if extension:
                value = (value + " ext. " + extension).strip()
            contacts.append({"type": "Fax" if kind == "fax" else "Phone", "value": value, "note": note})
        for field, kind in (("emails", "Email"), ("websites", "Website")):
            contacts.extend({"type": kind, "value": value, "note": kind + " printed for this listing"}
                            for value in prepared[field])
        prepared["contacts"] = contacts

        pages = set((details or {}).get("source_pages", []))
        def collect_pages(value):
            if isinstance(value, dict):
                if isinstance(value.get("source_page"), int):
                    pages.add(value["source_page"])
                for key, child in value.items():
                    if key != "context":
                        collect_pages(child)
            elif isinstance(value, list):
                for child in value:
                    collect_pages(child)
        collect_pages(prepared["source_evidence"])
        prepared["source_pages"] = sorted(pages)
        prepared["source_note"] = (("PDF page: " if len(pages) == 1 else "PDF pages: ")
                                   + ", ".join(map(str, sorted(pages)))) if pages else None

        notes = []
        if prepared["hierarchy"]:
            notes.append("Hierarchy: " + " > ".join(item["name"] for item in prepared["hierarchy"]))
        if prepared["qualifier"]:
            notes.append("Qualifier: " + prepared["qualifier"])
        if details:
            # A listing address takes precedence; keep the different parent context
            # in the occupation note. An inherited address appears only in Addresses.
            if prepared["address_text"] and details["address_text"]:
                notes.append("Organization office address: " + details["address_text"])
            for phone in details["phones"]:
                value = phone["number"] or ""
                if phone["extension"]:
                    value = (value + " ext. " + phone["extension"]).strip()
                label = " (" + phone["label"] + ")" if phone.get("label") else ""
                kind = "telephone/fax" if phone["kind"] == "phone_fax" else phone["kind"]
                notes.append("Organization " + kind + label + ": " + value)
            if details["emails"]:
                notes.append("Organization email: " + "; ".join(details["emails"]))
            if details["websites"]:
                notes.append("Organization website: " + "; ".join(details["websites"]))
        prepared["occupation_note"] = " | ".join(notes)

    def prepare_dbm_listing(self, record, config):
        """Keep every source row and mark which rows contain a named person."""
        name = record.get("printed_name") or self._dbm_text(record.get("chair_source"))
        if not name:
            name = self._dbm_join(record.get("name_lines", []))
        vacant = (
            bool(record.get("vacant")) or record.get("status") == "vacant"
            or str(name).upper() in {"VACANT", "(VACANT)"}
        )
        hierarchy = self._dbm_hierarchy(record)
        role = (
            record.get("role") or record.get("inherited_role")
            or self._dbm_text(record.get("role_source")) or self._dbm_join(record.get("role_lines", []))
        )
        if not role and record["record_kind"] == "committee" and name and not vacant:
            role = "Chairperson"
        if not role:
            role = next(
                (entry["fields"]["shared_role"] for entry in reversed(record.get("context", []))
                 if entry["fields"].get("shared_role")), None,
            )
        if not role and "senate" in record["source_path"] and "roster" in record["source_path"]:
            role = "Senator"
        prepared = {
            "record_kind": record["record_kind"],
            "is_person_record": bool(name) and not vacant,
            "vacant": vacant,
            "name_text": name or None,
            "role_text": role or None,
            "department_name": next((item["name"] for item in reversed(hierarchy) if item["kind"] == "department"), None),
            "organization_name": next(
                (item["name"] for item in reversed(hierarchy) if item["kind"] in {"organization", "department", "court", "mission"}), None,
            ),
            "office_text": self._dbm_text(record.get("office")) or self._dbm_join(record.get("office_lines", [])) or None,
            "address_text": self._dbm_join(record.get("address_lines", [])) or None,
            "qualifier": record.get("qualifier"),
            "hierarchy": hierarchy,
            "phones": [], "emails": [], "websites": [], "contact_unparsed": [],
            "source_path": deepcopy(record["source_path"]),
            "source_evidence": deepcopy(record),
        }
        self._dbm_contacts(record, prepared)
        self._dbm_organization_details(record, prepared)
        self._dbm_assignment_identity(prepared)
        return prepared

    def _dbm_identity_text(self, value):
        """Normalize typography only; never fuzzy-match people or institutions."""
        text = unicodedata.normalize("NFKC", self._dbm_text(value)).upper()
        text = text.translate(str.maketrans({"–": "-", "—": "-", "’": "'", "‘": "'", "“": '"', "”": '"'}))
        text = " ".join(text.split())
        text = re.sub(r"\s*([.,'-])\s*", r"\1", text)
        # PDF text can split the initial letter of this printed word.
        text = re.sub(r"\bO\s+FFICE\b", "OFFICE", text)
        text = re.sub(r"\bOFFI\s+(CE(?:RS?|S)?)\b", r"OFFI\1", text)
        return re.sub(r"\bOFFICEOF\b", "OFFICE OF", text)

    def _dbm_identity_person(self, value):
        name = unicodedata.normalize("NFKC", str(value or "")).strip()
        name = re.sub(r"^(?:(?:H\.\s*E\.|Hon\.|Dr\.|Mr\.|Mrs\.|Ms\.|Atty\.|Engr\.)\s*)+",
                      "", name, flags=re.IGNORECASE)
        parts = [part.strip() for part in name.split(",")]
        suffixes = []
        while len(parts) > 1 and re.fullmatch(r"(?:JR\.?|SR\.?|[IVX]+)", parts[-1], re.IGNORECASE):
            suffixes.insert(0, parts.pop())
        if len(parts) == 2:
            suffix = re.search(r"\s+(JR\.?|SR\.?|[IVX]+)$", parts[1], re.IGNORECASE)
            if suffix:
                suffixes.insert(0, suffix[1])
                parts[1] = parts[1][:suffix.start()].rstrip()
        if len(parts) == 2 and all(parts):
            name = parts[1] + " " + parts[0]
        else:
            name = " ".join(parts)
        name = " ".join([name, *suffixes])
        return self._dbm_identity_text(name.replace(".", ""))

    def _dbm_assignment_identity(self, prepared):
        """Build edition-independent assignment fields for the generic ID handler."""
        raw = prepared["source_evidence"]
        path = prepared["source_path"]
        clean = self._dbm_identity_text
        institution = clean(prepared["organization_name"])
        if "local_government_units" in path and "bangsamoro" not in path:
            institution = "PHILIPPINE LOCAL GOVERNMENT"
        elif "house_of_representatives" in path:
            institution = "PHILIPPINE HOUSE OF REPRESENTATIVES"
        elif "senate" in path:
            institution = "PHILIPPINE SENATE"

        territory = {}
        for field in ("province", "city", "municipality"):
            if clean(raw.get(field)):
                territory[field] = clean(raw[field])
        for entry in raw.get("context", []):
            for field in ("printed_place", "country", "locality"):
                value = clean(entry["fields"].get(field))
                if value:
                    territory[field] = value
        district = clean(raw.get("printed_district"))
        if district:
            district = re.sub(r"\s+DISTRICT$", "", district)
            district = re.sub(r"^(\d+)(?:ST|ND|RD|TH)$", r"\1", district)
            territory["district"] = district
        # Keep the district in mapped occupation context as well as in the key.
        if raw.get("printed_district"):
            prepared["district_text"] = self._dbm_text(raw["printed_district"])
            note = "District: " + prepared["district_text"]
            prepared["occupation_note"] = " | ".join(filter(None, [prepared["occupation_note"], note]))

        hierarchy = prepared["hierarchy"]
        explicit_units = any(item["kind"] in {"office", "committee"} for item in hierarchy)
        assemblies = {
            clean(assembly.get("heading"))
            for entry in raw.get("context", [])
            for assembly in entry["fields"].get("assemblies", [])
        }
        units, parties = set(), set()
        role_slug = re.sub(r"\W", "", clean(prepared["role_text"]))
        for item in hierarchy:
            kind, name = item["kind"], clean(item["name"])
            if kind == "posting":
                territory["posting"] = name
            elif kind == "party":
                parties.add(name)
            elif kind in {"office", "committee"}:
                # "Secretary General" and "Office of the Secretary General"
                # describe the same assignment on two different House pages.
                slug = re.sub(r"\W", "", name)
                if name != institution and slug not in {"OFFICEOF" + role_slug, "OFFICEOFTHE" + role_slug}:
                    units.add(name)
            elif kind == "group" and name != "OFFICE OF THE PRESIDENT":
                if (name in assemblies or name.startswith("OFFICE OF ")
                        or re.search(r"\bDIVISION\b", name)
                        or (not explicit_units and re.search(r"\b(?:DIVISION|SERVICE|COUNCIL)\b", name))):
                    units.add(name)

        qualifier = clean(prepared["qualifier"]).strip("() ")
        if "house_of_representatives" in path and qualifier == "LEGISLATIVE CARETAKER":
            qualifier = "CARETAKER"
        identity = {
            "person": self._dbm_identity_person(prepared["name_text"]),
            "institution": institution,
            "position": clean(prepared["role_text"]),
            "units": sorted(units),
            "territory": territory,
            "parties": sorted(parties),
            "qualifier": qualifier,
        }
        prepared["assignment_identity"] = identity
        prepared["assignment_key"] = json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _dbm_merge_assignment(self, target, occurrence):
        """Coalesce repeated listings without dropping source or contact evidence."""
        target.setdefault("source_evidence_rows", [deepcopy(target["source_evidence"])])
        target["source_evidence_rows"].append(deepcopy(occurrence["source_evidence"]))
        for field in ("phones", "emails", "websites", "contact_unparsed", "contacts", "hierarchy"):
            for value in occurrence[field]:
                if value not in target[field]:
                    target[field].append(deepcopy(value))
        addresses = target["addresses"] + occurrence["addresses"]
        explicit = [value for value in addresses if value["note"] == "Office address printed for this listing"]
        target["addresses"] = []
        for value in explicit or addresses:
            if value not in target["addresses"]:
                target["addresses"].append(deepcopy(value))
        for field in ("office_text", "address_text", "organization_details"):
            if not target.get(field) and occurrence.get(field):
                target[field] = deepcopy(occurrence[field])
        target["source_pages"] = sorted(set(target["source_pages"]) | set(occurrence["source_pages"]))
        pages = target["source_pages"]
        target["source_note"] = (("PDF page: " if len(pages) == 1 else "PDF pages: ")
                                 + ", ".join(map(str, pages))) if pages else None
        notes = list(dict.fromkeys(filter(None, [*target["occupation_note"].split(" | "),
                                                  *occurrence["occupation_note"].split(" | ")])))
        if explicit:
            for address in addresses:
                if address not in explicit:
                    note = "Organization office address: " + address["text"]
                    if note not in notes:
                        notes.append(note)
        target["occupation_note"] = " | ".join(notes)

    def prepare_dbm_directory(self, records, config):
        """Emit one Individual per assignment, retaining repeated source evidence."""
        people = []
        for raw in records:
            person = self.prepare_dbm_listing(raw, {})
            if not person["is_person_record"]:
                continue
            person["entity_type"] = "Individual"
            self.generate_composite_id(person, config["person_id"])
            people.append(person)
        # Chamber-wide headers are printed on the roster/leadership page, while
        # committee and representative subsections omit the same shared details.
        for chamber in ("senate", "house_of_representatives"):
            headers = [person["organization_details"] for person in people
                       if chamber in person["source_path"] and person.get("organization_details")]
            if not headers:
                continue
            first = headers[0]
            if any(header != first for header in headers):
                continue
            for person in people:
                if chamber in person["source_path"] and not person.get("organization_details"):
                    person["organization_details"] = deepcopy(first)
                    self._dbm_apply_office_context(person)
                    self._dbm_assignment_identity(person)
        assignments = {}
        for person in people:
            key = person["source_record_id"]
            if key in assignments:
                self._dbm_merge_assignment(assignments[key], person)
            else:
                assignments[key] = person
        return list(assignments.values())


    def preprocess(self, records, rules):
        records = list(records)

        if not rules:
            return records

        for rule in rules:
            if rule.get("level", "record") != "dataset":
                continue

            handler_name = rule["handler"]
            config = rule.get("config", {})
            handler = getattr(self, handler_name, None)

            if handler is None:
                raise ValueError(
                    f"No preprocessing handler found: {handler_name}"
                )

            records = handler(records, config)

        processed_records = []

        for record in records:
            for rule in rules:
                if rule.get("level", "record") != "record":
                    continue

                handler_name = rule["handler"]
                config = rule.get("config", {})
                handler = getattr(self, handler_name, None)

                if handler is None:
                    raise ValueError(
                        f"No preprocessing handler found: {handler_name}"
                    )

                record = handler(record, config)

            processed_records.append(record)

        return processed_records

    def fix_eu_vessel_multiline_rows(self, records, config):
        fixed_records = []

        for record in records:
            vessel_name_key = next(
                (
                    key
                    for key in record
                    if str(key).startswith("Vessel name at designation time")
                ),
                "Vessel name at designation time",
            )
            vessel_name = str(record.get(vessel_name_key, "")).strip()

            imo_number = str(
                record.get("IMO number", "")
            ).strip()

            date_value = str(
                record.get("Date of application", "")
            ).strip()

            link_value = str(
                record.get(
                    "Link to relevant EU Official Journal ",
                    "",
                )
            ).strip()

            if not vessel_name or not imo_number:
                continue

            if date_value == "#REF!":
                date_value = ""

            if link_value == "#REF!":
                link_value = ""

            record["Vessel name at designation time"] = vessel_name
            record["IMO number"] = imo_number
            record["Date of application"] = date_value
            record[
                "Link to relevant EU Official Journal "
            ] = link_value

            fixed_records.append(record)

        return fixed_records


    def merge_un_designated_vessel_rows(self, records, config):
        """Group UN 1718 PDF rows into one record per vessel."""

        def clean(value):
            if value is None:
                return ""
            return re.sub(r"\s+", " ", str(value)).strip()

        def split_numbered(value):
            parts = re.split(
                r"(?:^|\s)\d+\s*[:.]\s*",
                clean(value)
            )
            result = []
            seen = set()

            for part in parts:
                part = clean(part)

                if part and part.casefold() not in seen:
                    result.append(part)
                    seen.add(part.casefold())

            return result

        detail_fields = {
            "flag": "FlagRaw",
            "call sign": "CallSignRaw",
            "type": "VesselType",
            "other information": "OtherInformation",
            "picture link": "PictureLinkRaw",
            "designated as economic resources of": "EconomicResourceOf",
        }

        required_fields = {
            "RecordNumber",
            "VesselNameOrDetailLabel",
            "DetailValue",
            "IMOAndMMSI",
            "DesignationDate",
        }

        vessels = []
        current = None
        seen_numbers = set()

        for row_index, row in enumerate(records, start=1):
            if not isinstance(row, dict):
                raise ValueError(
                    f"UN vessels row {row_index} is not a dictionary."
                )

            if not required_fields.issubset(row):
                raise ValueError(
                    f"UN vessels row {row_index} has unexpected columns."
                )

            number = clean(row.get("RecordNumber"))
            label = clean(row.get("VesselNameOrDetailLabel"))

            if number:
                if not re.fullmatch(r"\d+", number):
                    raise ValueError(
                        f"Invalid UN vessel record number: {number}"
                    )

                if number in seen_numbers:
                    raise ValueError(
                        f"Duplicate UN vessel record number: {number}"
                    )

                names = split_numbered(label)

                if not names:
                    raise ValueError(
                        f"UN vessel {number} has no name."
                    )

                seen_numbers.add(number)

                current = dict(row)
                current["source_rows"] = [dict(row)]
                current["VesselName"] = names[0]
                current["VesselNames"] = [
                    {
                        "Name": name,
                        "NameType": (
                            "Primary Name" if index == 0 else "Alias"
                        ),
                    }
                    for index, name in enumerate(names)
                ]

                for field in detail_fields.values():
                    current[field] = None

                vessels.append(current)
                continue

            if not any(
                clean(row.get(field))
                for field in required_fields
            ):
                continue

            output_field = detail_fields.get(label.casefold())

            if current is None or output_field is None:
                raise ValueError(
                    f"Unexpected UN vessel detail at row "
                    f"{row_index}: {label!r}"
                )

            current["source_rows"].append(dict(row))
            value = clean(row.get("DetailValue"))

            # Join PDF line breaks inside picture URLs.
            if (
                output_field == "PictureLinkRaw"
                and value.lower().startswith(("http://", "https://"))
            ):
                value = re.sub(r"\s+", "", value)

            previous = current[output_field]

            if value:
                current[output_field] = (
                    f"{previous}\n{value}" if previous else value
                )

        if not vessels:
            raise ValueError(
                "No numbered UN vessel records were found."
            )

        seen_ids = set()

        for vessel in vessels:
            identifiers = clean(vessel.get("IMOAndMMSI"))
            imo_match = re.match(r"^(\d{7})(?!\d)", identifiers)
            imo = imo_match.group(1) if imo_match else None

            if (
                imo is None
                and identifiers.casefold() != "does not exist"
            ):
                raise ValueError(
                    f"Unexpected IMO value for vessel "
                    f"{vessel['RecordNumber']}: {identifiers!r}"
                )

            mmsi_parts = re.split(
                r"\bMMSI\s*:",
                identifiers,
                maxsplit=1,
                flags=re.IGNORECASE
            )

            vessel["IMO"] = imo
            vessel["MMSI"] = (
                list(dict.fromkeys(
                    re.findall(r"\b\d{9}\b", mmsi_parts[1])
                ))
                if len(mmsi_parts) == 2
                else []
            )

            vessel["Flags"] = split_numbered(
                vessel["FlagRaw"]
            )
            vessel["CallSigns"] = split_numbered(
                vessel["CallSignRaw"]
            )

            if imo:
                source_id = f"UN-1718-VESSEL-IMO-{imo}"
            else:
                name_key = vessel["VesselName"].casefold()
                name_hash = hashlib.sha256(
                    name_key.encode("utf-8")
                ).hexdigest()

                source_id = f"UN-1718-VESSEL-NAME-{name_hash}"

            if source_id in seen_ids:
                raise ValueError(
                    f"Duplicate UN vessel source identifier: {source_id}"
                )

            seen_ids.add(source_id)
            vessel["source_record_id"] = source_id

        for vessel in vessels:
            source_rows = vessel.get("source_rows")

            if not isinstance(source_rows, list) or not source_rows:
                raise ValueError(
                    "UN vessel record must contain non-empty source_rows."
                )

            measure_definitions = {
                "Measure_1718_8d_2270_12": (
                    "Asset Freeze",
                    "Asset freeze under paragraph 8(d) of resolution "
                    "1718 (2006) and paragraph 12 of resolution 2270 (2016).",
                ),
                "Measure_2321_12a": (
                    "Maritime Restriction",
                    "De-flagging under paragraph 12(a) of resolution "
                    "2321 (2016).",
                ),
                "Measure_2321_12d": (
                    "Asset Freeze",
                    "Asset freeze under paragraph 12(d) of resolution "
                    "2321 (2016).",
                ),
                "Measure_2371_6_2375_6": (
                    "Maritime Restriction",
                    "Port-entry restriction under paragraph 6 of resolution "
                    "2371 (2017) and paragraph 6 of resolution 2375 (2017).",
                ),
                "Measure_OMM_EconomicResources": (
                    "Asset Freeze",
                    "Listed as an economic resource controlled or operated "
                    "by Ocean Maritime Management (KPe.020); asset freeze "
                    "under paragraph 23 of resolution 2270 (2016).",
                ),
            }

            dates = []
            measures = []
            seen_dates = set()
            seen_measures = set()

            for row in source_rows:
                if not isinstance(row, dict):
                    raise ValueError(
                        "UN vessel source row is not a dictionary."
                    )

                designation_date = clean(
                    row.get("DesignationDate")
                )

                if designation_date.casefold() in {"", "na", "n/a"}:
                    designation_date = ""

                if (
                    designation_date
                    and designation_date not in seen_dates
                ):
                    dates.append({
                        "OriginalValue": designation_date,
                        "Type": "Designation Date",
                    })
                    seen_dates.add(designation_date)

                for field, definition in measure_definitions.items():
                    measure_type, note = definition
                    flag = clean(row.get(field)).casefold()

                    if flag in {"", "na", "n/a"}:
                        continue

                    if flag != "yes":
                        raise ValueError(
                            f"Unexpected UN measure value: "
                            f"{field}={flag!r}"
                        )

                    if not designation_date:
                        raise ValueError(
                            f"UN measure {field} has no designation date."
                        )

                    key = (field, designation_date)

                    if key in seen_measures:
                        continue

                    seen_measures.add(key)
                    measures.append({
                        "MeasureType": measure_type,
                        "EffectiveDate": designation_date,
                        "Note": note,
                    })

            if not dates or not measures:
                raise ValueError(
                    "UN vessel record has no designation dates or measures."
                )

            identifiers = []
            seen_identifiers = set()

            for identifier_type, values in (
                ("IMO Number", [vessel.get("IMO")]),
                ("MMSI", vessel.get("MMSI") or []),
                ("Call Sign", vessel.get("CallSigns") or []),
            ):
                for value in values:
                    number = clean(value)

                    if number.casefold() in {"", "na", "n/a"}:
                        continue

                    key = (identifier_type, number.casefold())

                    if key in seen_identifiers:
                        continue

                    seen_identifiers.add(key)
                    identifiers.append({
                        "Type": identifier_type,
                        "Number": number,
                    })

            aliases = [
                {
                    "Alias": clean(item.get("Name")),
                    "AliasType": "AKA",
                }
                for item in (vessel.get("VesselNames") or [])
                if item.get("NameType") == "Alias"
                and clean(item.get("Name"))
            ]

            seen_alias_names = {
                clean(vessel.get("VesselName")).casefold()
            }
            seen_alias_names.update(
                item["Alias"].casefold()
                for item in aliases
            )

            other_information = clean(
                vessel.get("OtherInformation")
            )

            # Extract names explicitly attributed to this vessel.
            # Example: "now operating as a DPRK flagged vessel named ..."
            for match in re.finditer(
                r"\bnow\s+operating\s+as\s+[^.\n]*?"
                r"\bvessel\s+named\s+(?:the\s+)?([^.\n]+)",
                other_information,
                flags=re.IGNORECASE,
            ):
                for value in re.split(
                    r"\s+or\s+",
                    match.group(1),
                    flags=re.IGNORECASE,
                ):
                    alias = clean(value)
                    alias_key = alias.casefold()

                    if alias_key in {"", "na", "n/a"}:
                        continue

                    if alias_key in seen_alias_names:
                        continue

                    aliases.append({
                        "Alias": alias,
                        "AliasType": "AKA",
                    })
                    seen_alias_names.add(alias_key)

            comments = []

            for field, comment_type in (
                ("OtherInformation", "Other Information"),
                ("EconomicResourceOf", "Designated Economic Resource Of"),
                ("FlagRaw", "Flags As Listed"),
                ("CallSignRaw", "Call Signs As Listed"),
                ("PictureLinkRaw", "Picture Link As Listed"),
            ):
                text = clean(vessel.get(field))

                if text.casefold() not in {"", "na", "n/a"}:
                    comments.append({
                        "type": comment_type,
                        "text": text,
                    })

            vessel["DesignationEntries"] = dates
            vessel["SanctionMeasures"] = measures
            vessel["VesselIdentifiers"] = identifiers
            vessel["VesselAliases"] = aliases
            vessel["VesselComments"] = comments

        return vessels

    def merge_dfat_split_records(self, records, config):
        grouped = {}
        designations_field = config.get("designations_field")

        single_value_fields = {
            "Reference",
            "Type",
            "Control Date",
            "Targeted Financial Sanction",
            "Travel Ban",
            "Arms Embargo",
            "Maritime Restriction",
        }

        for record in records:
            reference = str(
                record.get("Reference", "")
            ).strip()

            match = re.match(r"^(\d+)", reference)
            base_reference = (
                match.group(1) if match else reference
            )

            if base_reference not in grouped:
                grouped[base_reference] = {
                    "Reference": base_reference,
                    "Names": [],
                }

            merged = grouped[base_reference]

            if designations_field:
                committee = str(record.get("Committees", "")).strip()
                instrument = str(
                    record.get("Instrument of Designation", "")
                ).strip()
                designation = {
                    "Committees": "" if committee.upper() in EMPTY_VALUES else committee,
                    "instrument": "" if instrument.upper() in EMPTY_VALUES else instrument,
                }
                if any(designation.values()):
                    designations = merged.setdefault(designations_field, [])
                    if designation not in designations:
                        designations.append(designation)

            name_value = str(
                record.get(
                    "Name of Individual or Entity",
                    "",
                )
            ).strip()

            name_type = str(
                record.get("Name Type", "")
            ).strip()

            alias_strength = str(
                record.get("Alias Strength", "")
            ).strip()

            if name_value:
                name_item = {
                    "Name of Individual or Entity": name_value,
                    "Name Type": name_type,
                    "Alias Strength": alias_strength,
                }

                if name_item not in merged["Names"]:
                    merged["Names"].append(name_item)

            ignored_fields = {
                "Reference",
                "Name of Individual or Entity",
                "Name Type",
                "Alias Strength",
            }

            for field, value in record.items():
                if field in ignored_fields:
                    continue

                value = str(value).strip()

                if value.upper() in EMPTY_VALUES:
                    continue

                if field in single_value_fields:
                    if field not in merged:
                        merged[field] = value
                    continue

                if field not in merged:
                    merged[field] = []

                if value not in merged[field]:
                    merged[field].append(value)

        return list(grouped.values())

    def enrich_from_attachment(self, record, config):
        """Load a per-record detail file (keyed off a stub field) and return the
        list_detail shape ``{source_record_id, list, detail}``, so mapping reads
        ``list.*``/``detail.*``. Unchanged if the key or detail file is missing.

        config: attachments_dir, key_field, filename_template (default
        "{key}.json"), id_field/list_field/detail_field."""

        attachments_dir = config.get("attachments_dir")
        key_field = config.get("key_field")

        if not attachments_dir or not key_field:
            return record

        key = str(record.get(key_field, "")).strip()

        if not key:
            return record

        safe_key = key

        for char in '/\\:*?"<>|':
            safe_key = safe_key.replace(char, "-")

        filename = config.get(
            "filename_template", "{key}.json"
        ).format(key=safe_key)

        file_path = os.path.join(attachments_dir, filename)

        if not os.path.exists(file_path):
            return record

        with open(file_path, "r", encoding="utf-8") as handle:
            detail = json.load(handle)

        return {
            config.get("id_field", "source_record_id"): key,
            config.get("list_field", "list"): record,
            config.get("detail_field", "detail"): detail,
        }

    def detect_entity_type(self, record, config):
        input_field = config["input_field"]
        output_field = config.get(
            "output_field",
            "detected_entity_type",
        )

        name = str(record.get(input_field) or "").strip()

        organization_pattern = (
            r"(?i)(?:"
            r"\b(?:"
            r"INC|INCORPORATED|INCORPORATION|"
            r"CORP|CORPORATIONS?|CORPORATIION|"
            r"COMPANY|COMPANIES|LLC|LLP|LTD|LIMITED|OPC|"
            r"GMBH|AG|AB|JSC|PJSC|PTE|PVT|"
            r"OY|OU|TMI|FZE|FZC|SDN|BHD|"
            r"UNIVERSITY|INSTITUTE|ACADEMY|"
            r"FIRMS?|OFFICES?|LAW|LAWFIRM|ASSOCIATES|PARTNERS|"
            r"CPA|CPAS|ACCOUNTING|BOOKKEEPING|"
            r"AUDITING|CONSULTANCY|"
            r"CONSTRUCTION|BUSINESS|GROUP|GRUP|"
            r"TRADING|TRADE|STORES?|SHOPS?|SHOPPES?|"
            r"PAWNSHOP|WORKSHOP|JEWELRY|JEWELLERY|"
            r"JEWELS|MERCHANDISE|MDSE|REALTY|"
            r"BROKERAGE|SERVICES|SYSTEMS|"
            r"AIR|AIRLINES|AIRWAYS|AVIATION|AIRCRAFT|"
            r"TECHNOLOGY|TECHNOLOGIES|TECHNIK|"
            r"TECHNOLOGIC|TECH|"
            r"INTERNATIONAL|COMMUNICATION|"
            r"COMMUNICATIONS|TELECOM|"
            r"ELECTRONICS|ELECTRONIC|"
            r"INDUSTRY|INDUSTRIES|INDUSTRIAL|"
            r"MANUFACTURER|ENGINEERING|"
            r"LOGISTICS|CARGO|TRANSPORT|"
            r"TRANSPORTATION|FORWARDING|"
            r"COURIER|SHIPPING|"
            r"EQUIPMENT|MACHINERY|AUTOMATION|"
            r"COMPUTERS|LAB|LABS|PHOTONICS|"
            r"HARDWARE|SUPPLY|"
            r"EXPORTERS|IMPORTERS|"
            r"VERTRIEBS|ANSTALT"
            r")\b|"
            r"\b(?:L\.L\.C\.?|S\.R\.O\.?|"
            r"A\.G\.|S\.A\.|e\.K\.)(?=\W|$)|"
            r"\bCO\.(?=\W|$)|"
            r"\.NET\b"
            r")"
        )

        if re.search(organization_pattern, name):
            record[output_field] = "Entity"
            return record

        parsed_name = HumanName(name)

        has_person_name = bool(
            parsed_name.first and parsed_name.last
        )

        has_title_and_name = bool(
            parsed_name.title
            and (parsed_name.first or parsed_name.last)
        )

        record[output_field] = (
            "Individual"
            if has_person_name or has_title_and_name
            else "Entity"
        )

        return record

    def set_constant_field(self, record, config):
        """Stamp a fixed value; with `when_field`, only if that field matches `when_pattern` (default: non-blank)."""
        output_field = config["output_field"]
        value = config["value"]
        overwrite = config.get("overwrite", False)

        when_field = config.get("when_field")
        if when_field:
            condition_value = str(self._resolve_field(record, when_field) or "")
            if not re.search(config.get("when_pattern", r"\S"), condition_value):
                return record

        current_value = record.get(output_field)

        is_empty = (
            current_value is None
            or (
                isinstance(current_value, str)
                and not current_value.strip()
            )
        )

        if overwrite or is_empty:
            record[output_field] = value

        return record

    def build_url_from_template(self, record, config):
        output_field = config.get(
            "output_field",
            "SourceURL",
        )

        when_field = config.get("when_field")
        if when_field and not self._resolve_field(record, when_field):
            record.setdefault(output_field, "")
            return record

        template = config.get(
            "template"
        )

        if not template:
            raise ValueError(
                "build_url_from_template requires template."
            )

        overwrite = config.get(
            "overwrite",
            False,
        )

        current_value = record.get(
            output_field
        )

        if (
            not overwrite
            and current_value is not None
            and str(current_value).strip()
        ):
            return record

        template_fields = {
            field_name
            for _, field_name, _, _
            in Formatter().parse(template)
            if field_name
        }

        missing_fields = [
            field_name
            for field_name in template_fields
            if (
                record.get(field_name) is None
                or str(
                    record.get(field_name)
                ).strip() == ""
            )
        ]

        if missing_fields:
            raise ValueError(
                "Cannot build URL. Missing template "
                f"field(s): {', '.join(sorted(missing_fields))}"
            )

        record[output_field] = (
            template.format_map(record)
        )

        return record

    def generate_atc_unique_id(self, record, config):
        name_field = config.get("name_field", "name")

        resolution_field = config.get(
            "resolution_field",
            "atc_resolution_no",
        )

        output_field = config.get(
            "output_field",
            "unique_id",
        )

        prefix = config.get("prefix", "ATC")

        name = str(
            self._resolve_field(record, name_field, "")
        ).strip()
        resolution_text = str(
            self._resolve_field(record, resolution_field, "")
        ).strip()

        match = re.search(
            r"Resolution\s+No\.?\s*([0-9]+)",
            resolution_text,
            re.IGNORECASE,
        )

        resolution_no = (
            match.group(1) if match else "UNKNOWN"
        )

        name_hash = hashlib.md5(
            name.lower().encode("utf-8")
        ).hexdigest()[:10]

        record[output_field] = (
            f"{prefix}-{resolution_no}-{name_hash}"
        )

        return record

    def extract_name_from_url(self, record, config):
        input_field = config.get(
            "input_field",
            "detail_url",
        )

        output_field = config.get(
            "output_field",
            "extracted_name_from_url",
        )

        detail_url = str(
            record.get(input_field, "")
        ).strip()

        if not detail_url:
            record[output_field] = ""
            return record

        path = urlparse(detail_url).path.strip("/")
        slug = path.split("/")[-1]

        record[output_field] = unquote(slug).strip()

        return record

    def split_atc_date_and_place_of_birth(
        self,
        record,
        config,
    ):
        input_field = config.get(
            "input_field",
            "detail.date_and_place_of_birth",
        )

        date_output_field = config.get(
            "date_output_field",
            "atc_birth_date",
        )

        place_output_field = config.get(
            "place_output_field",
            "atc_birth_place",
        )

        value = record

        for part in input_field.split("."):
            if not isinstance(value, dict):
                value = ""
                break

            value = value.get(part, "")

        value = str(value).strip()

        if value.upper() in EMPTY_VALUES:
            record[date_output_field] = ""
            record[place_output_field] = ""
            return record

        parts = value.split(",", 1)
        first_part = parts[0].strip()
        remaining_part = (
            parts[1].strip() if len(parts) > 1 else ""
        )

        if first_part.upper() in EMPTY_VALUES:
            record[date_output_field] = ""
            record[place_output_field] = remaining_part
            return record

        if re.search(r"\d", first_part):
            record[date_output_field] = first_part
            record[place_output_field] = remaining_part
        else:
            record[date_output_field] = ""
            record[place_output_field] = value

        return record

    def filter_missing_required_field(self, records, config):
        required_field = config["field"]

        valid_records = []
        rejected_count = 0

        for record_index, record in enumerate(records, start=1):
            field_value = str(
                record.get(required_field, "")
            ).strip()

            if not field_value:
                rejected_count += 1

                print(
                    f"[WARNING] Record {record_index} skipped: "
                    f"{required_field} is missing."
                )

                continue

            valid_records.append(record)

        print(
            f"[VALIDATION] {rejected_count} invalid records skipped. "
            f"{len(valid_records)} valid records remaining."
        )

        return valid_records

    def generate_composite_id(
        self,
        record,
        config,
    ):
        fields = config["fields"]

        output_field = config.get(
            "output_field",
            "unique_id",
        )

        prefix = config.get(
            "prefix",
            "",
        )

        values = []

        for field in fields:
            resolved = record

            for part in field.split("."):
                resolved = (
                    resolved.get(part)
                    if isinstance(resolved, dict)
                    else None
                )

            value = str(resolved or "").strip()

            values.append(value.upper())

        raw_id = "|".join(values)

        digest = hashlib.sha256(
            raw_id.encode("utf-8")
        ).hexdigest()

        if prefix:
            record[output_field] = (
                f"{prefix}-{digest}"
            )
        else:
            record[output_field] = digest

        return record

    def explode_nested_records(self, records, config):
        """
        Fan a nested list out into one record per item (dataset-level).

        Turns a parent that carries a nested list (e.g. one election contest
        holding many candidates) into many flat records -- one per child --
        so each child can become its own watchlist member downstream. Nothing
        here is source-specific: any "parent object -> nested list of
        children" shape reuses it by writing config, not code.

        config:
            match_field   only explode parents whose match_field equals
                          match_value; omit to explode every parent.
            match_value   the value match_field must equal (compared as text).
            list_path     dot-path to the child list inside each parent,
                          e.g. "candidates.candidates".
            carry_fields  {source_dot_path: output_field} -- values read from
                          the parent by dot-path and copied onto every child,
                          so parent-level context (contest code, statistics)
                          rides along with each exploded record.

        A child that is not a dict is wrapped as {"value": child}. carry_fields
        use setdefault, so a child that already holds the key keeps its value.
        """
        match_field = config.get("match_field")
        match_value = config.get("match_value")
        list_path = config["list_path"]
        carry_fields = config.get("carry_fields", {})

        exploded = []

        for record in records:
            if (
                match_field is not None
                and str(record.get(match_field, "")) != str(match_value)
            ):
                continue

            children = record

            for part in list_path.split("."):
                children = (
                    children.get(part, {})
                    if isinstance(children, dict)
                    else {}
                )

            if not isinstance(children, list):
                continue

            carried = {}

            for source_path, output_field in carry_fields.items():
                value = record

                for part in source_path.split("."):
                    value = (
                        value.get(part)
                        if isinstance(value, dict)
                        else None
                    )

                carried[output_field] = value

            for child in children:
                new_record = (
                    dict(child)
                    if isinstance(child, dict)
                    else {"value": child}
                )

                for key, value in carried.items():
                    new_record.setdefault(key, value)

                exploded.append(new_record)

        return exploded

    def disambiguate_duplicate_keys(self, records, config):
        """Copy base_field to output_field; when the key_fields combination repeats
        in the dataset, append tiebreak_fields (joined by separator) to keep ids unique."""
        key_fields = config["key_fields"]
        base_field = config["base_field"]
        tiebreak_fields = config["tiebreak_fields"]
        output_field = config["output_field"]
        separator = config.get("separator", "|")

        def text(record, field_path):
            return str(self._resolve_field(record, field_path) or "").strip()

        def key_of(record):
            return tuple(text(record, field).upper() for field in key_fields)

        counts = {}

        for record in records:
            key = key_of(record)
            counts[key] = counts.get(key, 0) + 1

        for record in records:
            parts = [text(record, base_field)]

            if counts[key_of(record)] > 1:
                parts.extend(text(record, field) for field in tiebreak_fields)

            record[output_field] = separator.join(parts)

        return records

    def expand_regex_records(self, records, config):
        """Expand repeated named captures into records carrying their original source fields."""
        pattern = re.compile(config["pattern"])
        if not pattern.groupindex:
            raise ValueError("expand_regex_records requires named capture groups")

        expanded = []
        for record in records:
            value = str(self._resolve_field(record, config["input_field"]) or "")
            matches = [
                {key: (value or "").strip() for key, value in match.groupdict().items()}
                for match in pattern.finditer(value)
            ]
            if not matches:
                raise ValueError(
                    f"No records matched {config['input_field']}; refusing to omit the source record"
                )

            boundary_pattern = config.get("boundary_pattern")
            if boundary_pattern and len(list(re.finditer(boundary_pattern, value))) != len(matches):
                raise ValueError("Not every source record boundary produced an extracted record")

            reference_field = config.get("reference_field")
            if reference_field:
                references = self._resolve_field(record, reference_field) or []
                if isinstance(references, dict):
                    references = [references]
                expected = {
                    str(item.get(config["reference_key"]) or "").strip().casefold()
                    for item in references
                    if item.get(config["reference_key"])
                }
                actual = {item[config["match_key"]].casefold() for item in matches}
                if len(actual) != len(matches):
                    raise ValueError("Repeated source identity in extracted records")
                if expected - actual:
                    raise ValueError(f"Source names were not extracted: {sorted(expected - actual)}")

            for match in matches:
                expanded.append({**record, **match})

        return expanded

    def split_field_regex(self, record, config):
        """
        Split one field into several sibling fields via a named-group regex.

        The record-level counterpart of pre-normalization's SplitPatternHandler:
        instead of emitting a list of objects for an array field, it writes each
        captured group onto the SAME record as a plain field. Use it when a
        source packs several values into one string
        ("66. VILLAR, CAMILLE (NP)" -> ballot number, name, party) and those
        parts are needed early -- e.g. the ballot number becomes the record's
        external id, which must exist before the raw member is stored (so this
        cannot wait for pre-normalization). Generic: the regex and the
        group->field mapping live in config, so any packed-string field is
        handled by config, not code.

        config:
            input_field   field to read and split.
            pattern       regex with (?P<name>...) named groups.
            outputs       {group_name: output_field}. A group that did not
                          match (optional group, or no overall match) writes ""
                          so the output field always exists.
        """
        resolved = record
        for part in config["input_field"].split("."):
            resolved = resolved.get(part) if isinstance(resolved, dict) else None
        value = str(resolved or "").strip()
        match = re.match(config["pattern"], value)

        for group_name, output_field in config["outputs"].items():
            captured = match.group(group_name) if match else None
            record[output_field] = captured.strip() if captured else ""

        return record

    def resolve_reference(self, record, config):
        """
        Resolve a per-record key against a co-located list and project fields.

        Generic keyed lookup: derive a key from the record, find the one item in
        `list_field` whose own key equals it, then copy chosen sub-fields of that
        item onto the record. Any "row references one entry in an attached/shared
        list" shape reuses it by config, not code. Keys can be read verbatim or
        pulled out of free text with a regex, so an id embedded in a label
        ("... *12", "code: 14") works the same as a plain id field. No match
        leaves the outputs at `default` and keeps the record.

        config:
            key_field     dot-path to the value holding the record's key.
            key_pattern   optional regex; group(1) (else whole match) is the key.
            list_field    dot-path to the list of candidate items.
            item_key      sub-field of each item holding its key.
            item_pattern  optional regex applied to item_key before comparing.
            outputs       {item_subfield: output_field} copied from the match.
            default       written to every output when nothing matches ("").
        """

        def dig(obj, path):
            for part in path.split("."):
                obj = obj.get(part) if isinstance(obj, dict) else None
            return obj

        def key_of(text, pattern):
            text = "" if text is None else str(text)
            if not pattern:
                return text.strip()
            found = re.search(pattern, text)
            if not found:
                return ""
            return (found.group(1) if found.groups() else found.group(0)).strip()

        record_key = key_of(dig(record, config["key_field"]), config.get("key_pattern"))
        items = dig(record, config["list_field"]) or []
        if isinstance(items, dict):
            items = [items]

        chosen = None

        if record_key:
            for item in items:
                if not isinstance(item, dict):
                    continue
                if key_of(item.get(config["item_key"]), config.get("item_pattern")) == record_key:
                    chosen = item
                    break

        default = config.get("default", "")

        for item_subfield, output_field in config["outputs"].items():
            record[output_field] = (chosen or {}).get(item_subfield, default)

        return record

    def extract_labelled_ocr_fields(self, record, config):
        """Extract labelled, vertically stacked fields in one OCR column.

        input_field names a layout field under _ocr.fields. labels maps printed
        labels to output fields, in their expected top-to-bottom order. Values
        are assigned by geometry, including wrapped lines and values merged
        with their label. Matching ignores label case. A missing separator is
        accepted only at the label edge of the configured OCR column; a value
        beginning with the same letters must not become another label.
        Original OCR text/evidence is left untouched.
        """
        input_field = config["input_field"]
        labels = config["labels"]
        if (not isinstance(labels, dict) or not labels
                or any(not isinstance(k, str) or not k.strip()
                       or not isinstance(v, str) or not v.strip()
                       for k, v in labels.items())
                or len(set(labels.values())) != len(labels)
                or len({label.casefold() for label in labels}) != len(labels)):
            raise ValueError("OCR labels require distinct non-empty output fields.")
        evidence = record.get("_ocr", {}).get("fields", {}).get(input_field, {})
        lines = evidence.get("lines")
        if not isinstance(lines, list) or not lines:
            raise ValueError(f"Missing OCR column evidence: {input_field}")

        def top(line):
            return min(point[1] for point in line["box"])

        def center(line):
            return sum(point[1] for point in line["box"]) / 4

        def start(line):
            # Allow a value's baseline to differ slightly from its label.
            height = max(point[1] for point in line["box"]) - top(line)
            return top(line) - height / 4

        ordered = sorted(lines, key=lambda line: (top(line), min(p[0] for p in line["box"])))
        markers = []
        for line in ordered:
            text = line["text"].strip()
            area = evidence.get("region")
            left = min(point[0] for point in line["box"])
            height = max(point[1] for point in line["box"]) - top(line)
            at_label_edge = area is not None and left <= area[0] + max(height, 1)
            for label in sorted(labels, key=len, reverse=True):
                match = re.match(re.escape(label), text, flags=re.IGNORECASE)
                if match is None or (area is not None and not at_label_edge):
                    continue
                remainder = text[match.end():]
                if remainder and not remainder[0].isspace() and not at_label_edge:
                    continue
                markers.append((label, line, remainder.strip()))
                break
        if [label for label, _, _ in markers] != list(labels):
            raise ValueError(f"Missing, repeated or reordered OCR labels: {input_field}")

        marker_numbers = {line["line_number"] for _, line, _ in markers}
        values = [line for line in ordered if line["line_number"] not in marker_numbers]
        assigned = set()
        outputs = {}
        for index, (label, marker, inline_value) in enumerate(markers):
            lower = start(marker)
            upper = start(markers[index + 1][1]) if index + 1 < len(markers) else float("inf")
            matches = [line for line in values if lower <= center(line) < upper]
            assigned.update(line["line_number"] for line in matches)
            parts = ([inline_value] if inline_value else []) + [line["text"] for line in matches]
            value = " ".join(" ".join(parts).split())
            if not value:
                raise ValueError(f"Empty OCR value for {label!r} in {input_field}")
            outputs[labels[label]] = value
        if assigned != {line["line_number"] for line in values}:
            raise ValueError(f"Unassigned OCR text in column: {input_field}")
        record.update(outputs)
        return record

    def join_object_field_values(self, record, config):
        """Join nonblank text from an object or list into a separate top-level field.

        source_path is relative to the record and value_path to each object.
        Values retain source order. Missing or non-string values are skipped.
        """
        source_path = config["source_path"]
        value_path = config["value_path"]
        target_field = config["target_field"]
        separator = config.get("separator", "; ")

        if not target_field or "." in target_field or target_field == source_path.split(".", 1)[0]:
            raise ValueError("target_field must be a separate top-level field")

        items = self._resolve_field(record, source_path)
        if isinstance(items, dict):
            items = [items]
        if not isinstance(items, list):
            return record

        texts = []
        for item in items:
            value = self._resolve_field(item, value_path)
            if isinstance(value, str):
                value = value.strip()
                if value:
                    texts.append(value)

        if texts:
            record[target_field] = separator.join(texts)

        return record

    def normalize_empty_fields(self, record, config):
        """Normalize source empty markers in flat or dotted fields."""

        for field_path in config.get("fields", []):
            parts = field_path.split(".")
            parent = record

            for part in parts[:-1]:
                if not isinstance(parent, dict):
                    parent = None
                    break

                parent = parent.get(part)

            if not isinstance(parent, dict) or parts[-1] not in parent:
                continue

            value = parent.get(parts[-1])
            normalized = "" if value is None else str(value).strip()

            if normalized.upper() in EMPTY_VALUES:
                parent[parts[-1]] = ""

        return record

    @staticmethod
    def _resolve_field(record, field_path, default=None):
        value = record

        for part in str(field_path).split("."):
            if not isinstance(value, dict):
                return default

            value = value.get(part, default)

        return value
