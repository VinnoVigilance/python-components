from pathlib import Path
from typing import Any

from parsing.parserFactory import create_parser
from transforms.preProcessingEngine import PreProcessingEngine


class MediaRawRecordService:

    def process(
        self,
        source_config: dict[str, Any],
        records: list[dict[str, Any]] | None = None,
        source_file_path: str | Path | None = None,
    ) -> list[dict[str, Any]]:

        raw_records = self._get_raw_records(
            source_config=source_config,
            records=records,
            source_file_path=source_file_path,
        )

        raw_records = self._run_preprocessing(
            source_config=source_config,
            records=raw_records,
        )

        return raw_records

    def _get_raw_records(
        self,
        source_config: dict[str, Any],
        records: list[dict[str, Any]] | None,
        source_file_path: str | Path | None,
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

        parser = create_parser(
            file_type=parser_type
        )

        file_path = Path(source_file_path)

        if not file_path.exists():
            raise FileNotFoundError(
                f"Source file not found: {file_path}"
            )

        return list(
            parser.parse(
                file_path=file_path,
                config=source_config,
            )
        )

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