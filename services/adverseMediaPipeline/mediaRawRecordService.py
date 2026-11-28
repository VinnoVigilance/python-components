from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from scrapy.http import HtmlResponse

from ingestion.crawler.spiders.mediaSpider import MediaSpider
from parsing.parserFactory import create_parser
from transforms.preProcessingEngine import PreProcessingEngine
from utils.hashing import calculate_file_hash


class MediaRawRecordService:

    def process_acquired_record(
        self,
        source_config: dict[str, Any],
        acquired_record: dict[str, Any],
    ) -> list[dict[str, Any]]:

        return self._run_preprocessing(
            source_config=source_config,
            records=self.extract_acquired_record(
                source_config=source_config,
                acquired_record=acquired_record,
            ),
        )

    def extract_acquired_record(
        self,
        source_config: dict[str, Any],
        acquired_record: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Raw fields of one acquired record; a stored PDF adds its parsed text."""

        extracted = acquired_record.get(
            "extracted"
        )

        file_path = acquired_record.get(
            "detail_file_path"
        )

        if extracted is None:
            records = self.extract(
                source_config=source_config,
                source_file_path=file_path,
            )
        else:
            record = dict(extracted)
            parser_type = str(
                (source_config.get("parser") or {}).get("type", "")
            ).strip().lower()
            if parser_type == "pdf" and file_path:
                for parsed in self.extract(
                    source_config=source_config,
                    source_file_path=file_path,
                ):
                    record.update(parsed)
            records = [record]

        for record in records:
            self._parse_document_attachments(record, source_config)
        return records

    def _parse_document_attachments(self, record: dict[str, Any], source_config: dict[str, Any]) -> None:
        """Add optional attachment text without turning one row into many publications.

        Portable manifests contain Seaweed references; local acquisition also has
        local paths. Parser or retrieval failures retain metadata and remain visible.
        """
        settings = source_config.get("document_download", {})
        parser_settings = settings.get("parser")
        if not parser_settings:
            return
        text_parts = []
        parsed_documents = []
        errors = list(record.get("DocumentErrors") or [])
        with TemporaryDirectory(prefix="media_attachments_") as temporary:
            for index, document in enumerate(record.get("DocumentFiles") or []):
                try:
                    local_path = document.get("local_path")
                    if not local_path or not Path(local_path).is_file():
                        if not document.get("storage_path"):
                            raise FileNotFoundError("Attachment has no local file or durable storage reference")
                        from infrastructure.storage import seaweedClient
                        local_path = seaweedClient.download_file(
                            storage_path=document["storage_path"],
                            destination_path=Path(temporary) / f"{index}.pdf",
                        )
                    if (document.get("file_hash")
                            and calculate_file_hash(local_path) != document["file_hash"]):
                        raise ValueError("Attachment bytes do not match the stored file hash")
                    parsed = self.extract(
                        source_config={"parser": parser_settings}, source_file_path=local_path)
                    if len(parsed) != 1:
                        raise ValueError("Attachment text mode must yield exactly one record")
                    text = parsed[0].get("BodyText") or ""
                    text_parts.append(f"Document: {document['url']}\n{text}")
                    parsed_documents.append({"url": document["url"], **parsed[0]})
                except Exception as error:
                    errors.append({"url": document.get("url"), "stage": "parse", "error": str(error)})
        record["DocumentTexts"] = parsed_documents
        record["DocumentBodyText"] = "\n\n".join(text_parts)
        record["DocumentErrors"] = errors

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

        for record in raw_records:
            self._parse_document_attachments(record, source_config)

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
