from pathlib import Path
from typing import Any

from scrapy.http import HtmlResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from parsing.parserFactory import create_parser
from transforms.preProcessingEngine import PreProcessingEngine


class MediaRawRecordService:

    def process_acquired_record(
        self,
        source_config: dict[str, Any],
        acquired_record: dict[str, Any],
    ) -> list[dict[str, Any]]:

        extracted = acquired_record.get(
            "extracted"
        )

        if extracted is not None:
            return self.process(
                source_config=source_config,
                records=[
                    extracted
                ],
            )

        return self.process(
            source_config=source_config,
            source_file_path=(
                acquired_record.get(
                    "detail_file_path"
                )
            ),
        )

    def process(
        self,
        source_config: dict[str, Any],
        records: list[dict[str, Any]] | None = None,
        source_file_path: str | Path | None = None,
        source_url: str | None = None,
    ) -> list[dict[str, Any]]:

        raw_records = self.extract(
            source_config=source_config,
            records=records,
            source_file_path=source_file_path,
            source_url=source_url,
        )

        raw_records = self._run_preprocessing(
            source_config=source_config,
            records=raw_records,
        )

        return raw_records

    def extract(
        self,
        source_config: dict[str, Any],
        records: list[dict[str, Any]] | None = None,
        source_file_path: str | Path | None = None,
        source_url: str | None = None,
    ) -> list[dict[str, Any]]:
        """Extract source fields without applying preprocessing."""

        return self._get_raw_records(
            source_config=source_config,
            records=records,
            source_file_path=source_file_path,
            source_url=source_url,
        )

    def _get_raw_records(
        self,
        source_config: dict[str, Any],
        records: list[dict[str, Any]] | None,
        source_file_path: str | Path | None,
        source_url: str | None,
    ) -> list[dict[str, Any]]:

        if records is not None:
            return list(records)

        if source_file_path is None:
            raise ValueError(
                "Either records or source_file_path must be provided."
            )

        parser_config = source_config.get("parser")

        if not parser_config:
            raise ValueError(
                "Parser configuration is required for file-based media sources."
            )

        parser_type = parser_config.get("type")

        if not parser_type:
            raise ValueError(
                "parser.type is required."
            )

        file_path = Path(source_file_path)

        if not file_path.exists():
            raise FileNotFoundError(
                f"Source file not found: {file_path}"
            )

        if (
            str(parser_type).strip().lower() == "html"
            and source_config.get("extraction")
        ):
            return [
                self._extract_media_html(
                    source_config=source_config,
                    file_path=file_path,
                    source_url=(
                        source_url
                        or source_config.get("url")
                        or file_path.as_uri()
                    ),
                )
            ]

        parser = create_parser(
            file_type=parser_type
        )

        return list(
            parser.parse(
                file_path=file_path,
                config=source_config,
            )
        )

    @staticmethod
    def _extract_media_html(
        source_config: dict[str, Any],
        file_path: Path,
        source_url: str,
    ) -> dict[str, Any]:
        """Reuse MediaSpider's configured detail extraction for Raw HTML."""

        response = HtmlResponse(
            url=source_url,
            body=file_path.read_bytes(),
            encoding="utf-8",
        )

        spider = MediaSpider(
            task=None,
            source_config=source_config,
            storage=None,
            records=[],
            discovery_service=None,
        )

        record = spider._extract_record(
            response
        )

        record["SourceURL"] = source_url

        source_record_id = (
            spider._extract_source_record_id(
                source_url
            )
        )

        if source_record_id:
            record["SourceRecordId"] = (
                source_record_id
            )

        return record

    def _run_preprocessing(
        self,
        source_config: dict[str, Any],
        records: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:

        preprocessing_rules = source_config.get(
            "preprocessing"
        )

        if not preprocessing_rules:
            return records

        return PreProcessingEngine().preprocess(
            records=records,
            rules=preprocessing_rules,
        )
