from pathlib import Path

import pdfplumber


class PdfParser:
    """
    PDF parser for tabular watchlist files.

    Output:
        A list of dictionaries.

    Important:
        - It does NOT add metadata fields such as source_page or source_table.
        - It only returns columns that exist in the PDF table.
        - entity_type should be added later by preprocessing, not here.
    """

    def parse(self, file_path, config=None):
        """
        Parse a PDF file and return table rows as raw records.

        This method is compatible with WatchlistPipeline:

            raw_records = self.parser.parse(
                file_path=downloaded_file_path,
                config=self.config
            )

        Args:
            file_path: Path to the downloaded PDF file.
            config: Optional pipeline config. Not required for basic parsing.

        Returns:
            list[dict]: Raw records extracted from PDF tables.
        """

        pdf_path = Path(file_path)

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        records = []
        global_headers = None

        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)

            print(f"PDF opened: {pdf_path.name}")
            print(f"Total pages: {total_pages}")

            for page_number, page in enumerate(pdf.pages, start=1):
                print(f"Processing page {page_number}/{total_pages} ...")

                tables = page.extract_tables()

                if not tables:
                    print(f"  No table found on page {page_number}")
                    continue

                print(f"  Found {len(tables)} table(s) on page {page_number}")

                for table_index, table in enumerate(tables, start=1):
                    if not table:
                        print(f"  Table {table_index} is empty")
                        continue

                    print(
                        f"  Table {table_index}: "
                        f"{len(table)} row(s)"
                    )

                    if global_headers is None:
                        global_headers = self._build_headers(table[0])
                        rows = table[1:]

                        print("  Header detected from first table:")
                        print(f"  {global_headers}")
                    else:
                        if self._is_same_header(table[0], global_headers):
                            rows = table[1:]
                            print("  Repeated header skipped")
                        else:
                            rows = table

                    for row in rows:
                        record = self._row_to_record(row, global_headers)

                        if record:
                            records.append(record)

        print(f"Parsing finished. Total records: {len(records)}")

        return records
    
    def parse_text(self, file_path):
        pdf_path = Path(file_path)

        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")

        pages_text = []

        with pdfplumber.open(pdf_path) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                text = page.extract_text() or ""

                if text.strip():
                    pages_text.append(
                        f"\n--- PAGE {page_number} ---\n{text.strip()}"
                    )

        return "\n".join(pages_text)

    def _row_to_record(self, row, headers):
        """
        Convert one PDF table row to a dictionary.

        No extra fields are added here.
        Only PDF columns are used as keys.
        """

        if not row or not headers:
            return None

        record = {}
        has_value = False

        for idx, header in enumerate(headers):
            if not header:
                continue

            value = row[idx] if idx < len(row) else None
            value = self._clean_value(value)

            record[header] = value

            if value not in [None, ""]:
                has_value = True

        if not has_value:
            return None

        return record

    def _build_headers(self, header_row):
        """
        Clean header row extracted from PDF.
        """

        headers = []

        for header in header_row:
            clean_header = self._clean_header(header)
            headers.append(clean_header)

        return headers

    def _clean_header(self, header):
        if header is None:
            return None

        header = str(header).strip()

        if not header:
            return None

        return (
            header
            .replace("\n", " ")
            .replace(".", "")
            .strip()
        )

    def _clean_value(self, value):
        if value is None:
            return None

        if isinstance(value, str):
            return value.replace("\n", " ").strip()

        return value

    def _is_same_header(self, row, headers):
        """
        Detect repeated table headers on later pages.
        """

        if not row or not headers:
            return False

        cleaned_row = [
            self._clean_header(cell)
            for cell in row
        ]

        cleaned_row = [
            cell for cell in cleaned_row
            if cell
        ]

        cleaned_headers = [
            self._clean_header(header)
            for header in headers
        ]

        cleaned_headers = [
            header for header in cleaned_headers
            if header
        ]

        if not cleaned_row or not cleaned_headers:
            return False

        matched = 0

        for cell in cleaned_row:
            if cell in cleaned_headers:
                matched += 1

        return matched >= 2