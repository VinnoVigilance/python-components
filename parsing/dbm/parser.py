"""Pipeline entry point for positioned DBM directory listings."""

from pathlib import Path

from .directory import extract_directory
from .records import iter_listings


class DbmDirectoryParser:
    def parse(self, file_path, config=None):
        pdf_path = Path(file_path)
        if not pdf_path.is_file():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        directory = extract_directory(pdf_path)
        return list(iter_listings(directory))
