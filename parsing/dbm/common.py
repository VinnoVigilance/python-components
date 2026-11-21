"""Shared printed vacancy and contact recognition for the DBM readers."""

import re


VACANCIES = {"VACANT", "(VACANT)"}
CONTACT = re.compile(r"\(?\d|loc\.|https?://|www\.", re.IGNORECASE)
EMAIL = re.compile(r"[\w.+%-]+@[\w-]+(?:\.[\w-]+)+")
EMAIL_START = re.compile(r"[\w.+%-]+@")
DOMAIN = re.compile(r"(?:[\w-]+\.)+(?:gov\.ph|org|com|net)", re.IGNORECASE)


def is_contact(value):
    return (
        CONTACT.match(value)
        or EMAIL.fullmatch("".join(value.split()).rstrip(";,:"))
        or EMAIL_START.fullmatch(value)
        or DOMAIN.fullmatch(value.rstrip(";,:"))
    )


def contact(text):
    # Retain printed addresses even when malformed (e.g. speaker@house,gov.ph).
    return is_contact(text) or "@" in text.partition(" ")[0]
