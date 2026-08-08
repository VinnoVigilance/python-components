"""Select a source-file parser by physical file type."""

from parsing.htmlParser import HtmlParser
from parsing.jsonlParser import JsonlParser
from parsing.pdfParser import PdfParser
from parsing.tabularParser import TabularParser
from parsing.xmlParser import XmlParser


PARSER_REGISTRY = {
    "xml": XmlParser,
    "pdf": PdfParser,
    "html": HtmlParser,
    "htm": HtmlParser,
    "jsonl": JsonlParser,
    "csv": TabularParser,
    "xlsx": TabularParser,
    "xls": TabularParser,
}


def create_parser(file_type: str):
    normalized = file_type.strip().lower().lstrip(".")
    parser_class = PARSER_REGISTRY.get(normalized)
    if parser_class is None:
        supported = ", ".join(sorted(PARSER_REGISTRY))
        raise ValueError(f"Unsupported file type: {file_type}. Supported types: {supported}")
    return parser_class()
