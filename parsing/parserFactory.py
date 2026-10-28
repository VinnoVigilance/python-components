# parsing/parserFactory.py

from parsing.xmlParser import XmlParser
from parsing.pdfParser import PdfParser
from parsing.tabularParser import TabularParser
from parsing.jsonlParser import JsonlParser
from parsing.jsonParser import JsonParser
from parsing.ocrParser import OcrParser, IMAGE_TYPES


PARSER_REGISTRY = {
    "xml": XmlParser,
    "pdf": PdfParser,
    "csv": TabularParser,
    "xlsx": TabularParser,
    "xls": TabularParser,
    "jsonl": JsonlParser,
    "json": JsonParser,
}

PARSER_REGISTRY.update({file_type: OcrParser for file_type in IMAGE_TYPES})


def create_parser(file_type: str):
    normalized_file_type = file_type.strip().lower()

    parser_class = PARSER_REGISTRY.get(normalized_file_type)

    if parser_class is None:
        supported_types = ", ".join(sorted(PARSER_REGISTRY))

        raise ValueError(
            f"Unsupported file type: {file_type}. "
            f"Supported types: {supported_types}"
        )

    return parser_class()
