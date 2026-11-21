"""Extract a DBM government officials directory as hierarchical JSON."""

from functools import partial

from pdfplumber.utils.exceptions import PdfminerException
from pypdfium2 import PdfiumError

from . import (
    bangsamoro,
    executive,
    house,
    house_officials,
    judiciary,
    local_government,
    representatives,
    senate,
)


def read_local_government_table(pdf_path, table):
    first, last = local_government.find_table_pages(pdf_path, table)
    return local_government.read(pdf_path, first, last, table)


DIRECTORY_SECTIONS = {
    "national_government": {
        "executive_branch": {
            "office_of_the_president": partial(executive.read, office="president"),
            "office_of_the_vice_president": partial(executive.read, office="vice-president"),
            "presidential_communications": partial(executive.read, office="presidential-communications"),
            "other_executive_offices": partial(executive.read, office="other-executive-offices"),
            "department_rosters": executive.read_departments,
            "constitutional_offices": executive.read_constitutional_offices,
            "corporations_and_colleges": executive.read_corporations_and_colleges,
        },
        "legislative_branch": {
            "senate": senate.read,
            "house_of_representatives": {
                "leadership_and_secretariat": house_officials.read,
                "district_representatives": house.read_districts,
                "party_lists_and_committees": house.read_party_lists_and_committees,
            },
        },
        "judicial_branch": judiciary.read,
    },
    "local_government_units": {
        "provincial_governments": partial(read_local_government_table, table="provincial"),
        "city_governments": partial(read_local_government_table, table="city"),
        "municipal_governments": partial(read_local_government_table, table="municipal"),
        "bangsamoro": bangsamoro.read,
    },
    "diplomatic_and_consular_missions": representatives.read_missions,
    "international_organizations": representatives.read_organizations,
}


def read_sections(pdf_path, sections, parent=()):
    result = {}
    for name, reader in sections.items():
        section = (*parent, name)
        if isinstance(reader, dict):
            result[name] = read_sections(pdf_path, reader, section)
            continue
        try:
            result[name] = reader(pdf_path)
        except (OSError, ValueError, PdfiumError, PdfminerException) as error:
            raise ValueError(f"{' / '.join(section)}: {error}") from error
    return result


def extract_directory(pdf_path):
    """Return each section reader's output under its directory heading."""
    return {"source": str(pdf_path), **read_sections(pdf_path, DIRECTORY_SECTIONS)}
