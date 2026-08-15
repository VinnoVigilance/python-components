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
        - Source-specific business logic must not be implemented here.
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
            file_path:
                Path to the downloaded PDF file.

            config:
                Optional pipeline config.

                Supported parser configuration:

                {
                    "parser_config": {
                        "expected_headers": [
                            "REGION",
                            "PROVINCE",
                            "P/C/M",
                            "POSITION",
                            "NAME"
                        ]
                    }
                }

                If expected_headers is not provided, the parser keeps
                the previous behavior and uses the first row of the
                first detected table as the header.

        Returns:
            list[dict]:
                Raw records extracted from PDF tables.
        """

        pdf_path = Path(file_path)

        if not pdf_path.exists():
            raise FileNotFoundError(
                f"PDF file not found: {pdf_path}"
            )

        config = config or {}

        parser_config = config.get(
            "parser_config",
            {}
        )

        expected_headers = parser_config.get(
            "expected_headers"
        )

        records = []
        global_headers = None

        with pdfplumber.open(pdf_path) as pdf:
            total_pages = len(pdf.pages)

            for page in pdf.pages:
                tables = page.extract_tables()

                if not tables:
                    continue

                for table in tables:
                    if not table:
                        continue

                    table = self._clean_table(table)

                    if not table:
                        continue

                    if global_headers is None:

                        if expected_headers:
                            header_index = self._find_header_row(
                                table=table,
                                expected_headers=expected_headers
                            )

                            if header_index is None:
                                continue

                            global_headers = self._build_headers(
                                table[header_index]
                            )

                            rows = table[header_index + 1:]

                        else:
                            # Backward-compatible behavior.
                            # Sources such as DNFBP continue to work
                            # exactly as before.
                            global_headers = self._build_headers(
                                table[0]
                            )

                            rows = table[1:]

                    else:
                        header_index = self._find_repeated_header(
                            table=table,
                            headers=global_headers,
                            expected_headers=expected_headers
                        )

                        if header_index is not None:
                            rows = table[header_index + 1:]
                        else:
                            rows = table

                    for row in rows:
                        record = self._row_to_record(
                            row=row,
                            headers=global_headers
                        )

                        if record:
                            records.append(record)

        print(
            f"PDF {pdf_path.name}: "
            f"{total_pages} pages processed, "
            f"{len(records)} records parsed."
        )

        return records

    def parse_text(self, file_path):
        """
        Extract plain text from all pages of a PDF.

        Returns:
            str:
                Extracted text separated by page markers.
        """

        pdf_path = Path(file_path)

        if not pdf_path.exists():
            raise FileNotFoundError(
                f"PDF file not found: {pdf_path}"
            )

        pages_text = []

        with pdfplumber.open(pdf_path) as pdf:
            for page_number, page in enumerate(
                pdf.pages,
                start=1
            ):
                text = page.extract_text() or ""

                if text.strip():
                    pages_text.append(
                        f"\n--- PAGE {page_number} ---\n"
                        f"{text.strip()}"
                    )

        return "\n".join(pages_text)

    def _find_header_row(
        self,
        table,
        expected_headers
    ):
        """
        Find the header row in a PDF table using configured
        expected headers.

        This is useful for PDFs where title rows or other content
        appear before the actual table header.

        Example:

            LIST OF ELECTED LOCAL OFFICIALS
            2025-2028

            REGION | PROVINCE | P/C/M | POSITION | NAME

        Args:
            table:
                Extracted PDF table.

            expected_headers:
                List of headers expected in the real table header.

        Returns:
            int | None:
                Index of the detected header row.
        """

        if not table or not expected_headers:
            return None

        expected = {
            self._normalize_header_for_matching(header)
            for header in expected_headers
            if self._normalize_header_for_matching(header)
        }

        if not expected:
            return None

        for index, row in enumerate(table):
            if not row:
                continue

            cleaned_row = {
                self._normalize_header_for_matching(cell)
                for cell in row
                if self._normalize_header_for_matching(cell)
            }

            if not cleaned_row:
                continue

            if expected.issubset(cleaned_row):
                return index

        return None

    def _find_repeated_header(
        self,
        table,
        headers,
        expected_headers=None
    ):
        """
        Detect a repeated table header on later pages/tables.

        If expected_headers is configured, the parser searches the
        table for that header.

        Otherwise it preserves the previous behavior and checks
        the first row only.
        """

        if not table:
            return None

        if expected_headers:
            return self._find_header_row(
                table=table,
                expected_headers=expected_headers
            )

        if self._is_same_header(
            table[0],
            headers
        ):
            return 0

        return None

    def _row_to_record(
        self,
        row,
        headers
    ):
        """
        Convert one PDF table row to a dictionary.

        No extra fields are added here.
        Only PDF columns are used as keys.
        """

        if not row or not headers:
            return None

        row = self._trim_trailing_empty_cells(row)

        record = {}
        has_value = False

        for idx, header in enumerate(headers):
            if not header:
                continue

            value = (
                row[idx]
                if idx < len(row)
                else None
            )

            value = self._clean_value(value)

            record[header] = value

            if value not in [None, ""]:
                has_value = True

        if not has_value:
            return None

        return record

    def _build_headers(self, header_row):
        """
        Clean a header row extracted from PDF.
        """

        header_row = self._trim_trailing_empty_cells(
            header_row
        )

        headers = []

        for header in header_row:
            clean_header = self._clean_header(header)
            headers.append(clean_header)

        return headers

    def _clean_table(self, table):
        """
        Perform only generic structural cleanup on an extracted table.

        Empty rows are removed and trailing empty cells are trimmed.

        No source-specific transformation is performed here.
        """

        cleaned_table = []

        for row in table:
            if not row:
                continue

            row = self._trim_trailing_empty_cells(row)

            if not row:
                continue

            if not self._row_has_value(row):
                continue

            cleaned_table.append(row)

        return cleaned_table

    def _trim_trailing_empty_cells(self, row):
        """
        Remove empty cells from the end of a table row.

        Some PDFs are extracted with an additional empty column
        at the end of the table. Removing only trailing empty cells
        is safe and does not modify meaningful column positions.
        """

        if row is None:
            return []

        cleaned_row = list(row)

        while cleaned_row:
            last_value = self._clean_value(
                cleaned_row[-1]
            )

            if last_value not in [None, ""]:
                break

            cleaned_row.pop()

        return cleaned_row

    def _row_has_value(self, row):
        """
        Return True if at least one cell in the row contains data.
        """

        for value in row:
            cleaned_value = self._clean_value(value)

            if cleaned_value not in [None, ""]:
                return True

        return False

    def _clean_header(self, header):
        """
        Clean a PDF header while preserving its original meaning.
        """

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

    def _normalize_header_for_matching(
        self,
        header
    ):
        """
        Normalize a header only for comparison.

        This does not change the final header written to records.
        """

        cleaned_header = self._clean_header(header)

        if not cleaned_header:
            return None

        return " ".join(
            cleaned_header.upper().split()
        )

    def _clean_value(self, value):
        """
        Clean a table cell value.
        """

        if value is None:
            return None

        if isinstance(value, str):
            value = (
                value
                .replace("\n", " ")
                .strip()
            )

            return value

        return value

    def _is_same_header(
        self,
        row,
        headers
    ):
        """
        Detect repeated table headers on later pages.

        This method keeps the behavior used by existing PDF sources
        that do not provide expected_headers in parser_config.
        """

        if not row or not headers:
            return False

        cleaned_row = [
            self._normalize_header_for_matching(cell)
            for cell in row
        ]

        cleaned_row = [
            cell
            for cell in cleaned_row
            if cell
        ]

        cleaned_headers = [
            self._normalize_header_for_matching(header)
            for header in headers
        ]

        cleaned_headers = [
            header
            for header in cleaned_headers
            if header
        ]

        if not cleaned_row or not cleaned_headers:
            return False

        matched = 0

        for cell in cleaned_row:
            if cell in cleaned_headers:
                matched += 1

        return matched >= 2