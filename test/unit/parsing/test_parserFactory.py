"""
Unit tests for parsing/parserFactory.py

The factory maps a file type ("xml", "xlsx", ...) to the parser class that
handles it. These tests check the mapping, the case/whitespace tolerance, and
the error raised for an unknown type.
"""

import pytest

from parsing.parserFactory import create_parser
from parsing.tabularParser import TabularParser
from parsing.xmlParser import XmlParser

pytestmark = pytest.mark.unit


def test_creates_xml_parser():
    assert isinstance(create_parser("xml"), XmlParser)


def test_spreadsheet_types_share_the_tabular_parser():
    assert isinstance(create_parser("xlsx"), TabularParser)
    assert isinstance(create_parser("csv"), TabularParser)


def test_file_type_is_case_and_whitespace_insensitive():
    assert isinstance(create_parser("  XLSX  "), TabularParser)


def test_unknown_type_raises_with_helpful_message():
    with pytest.raises(ValueError) as exc:
        create_parser("docx")

    message = str(exc.value)
    assert "docx" in message
    assert "Supported types" in message
