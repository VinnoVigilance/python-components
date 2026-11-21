"""Application entry point for news entity extraction."""

from __future__ import annotations

from intelligence.entityExtraction.contract import ExtractionResult
from intelligence.entityExtraction.extractor import EntityExtractor


class MediaEntityExtractionService:
    def __init__(
        self,
        model_dir=None,
        threads=4,
        *,
        extractor=None,
    ):
        self._extractor = (
            extractor
            if extractor is not None
            else EntityExtractor(model_dir, threads)
        )

    def extract(
        self,
        *,
        title: str | None,
        body: str | None,
    ) -> ExtractionResult:
        return self._extractor.extract(
            title=title,
            body=body,
        )

    def extract_many(
        self,
        articles,
        *,
        batch_size=32,
    ) -> list[ExtractionResult]:
        return self._extractor.extract_many(
            articles,
            batch_size=batch_size,
        )

    @property
    def metadata(self):
        return self._extractor.metadata

    def close(self):
        self._extractor.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()