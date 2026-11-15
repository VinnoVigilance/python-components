"""Public output structure for news entity extraction."""

from __future__ import annotations

from typing import Literal, TypedDict


MODEL_KEYS = (
    "gliner25_policy",
    "gliner21",
    "xlmr",
)

ENTITY_TYPES = {
    "INDIVIDUAL",
    "ORGANIZATION",
}


class EntityExtractionError(RuntimeError):
    """Raised when entity extraction cannot be completed."""

    def __init__(
        self,
        message: str,
        *,
        model_key: str | None = None,
    ):
        self.model_key = model_key

        full_message = (
            f"{model_key}: {message}"
            if model_key
            else message
        )

        super().__init__(full_message)


class Mention(TypedDict):
    """One accepted occurrence of a name in the article."""

    text: str
    field: Literal["title", "body"]

    start: int
    end: int

    evidenceSentence: str

    votes: int
    models: list[str]
    scores: dict[str, float]


class Entity(TypedDict):
    """One person or organization, grouped within one article."""

    entityType: Literal["INDIVIDUAL", "ORGANIZATION"]

    memberName: str
    aliases: list[str]

    mentions: list[Mention]
    groupingEvidence: list[dict]


class ExtractionResult(TypedDict):
    """Complete extraction result for one article."""

    status: Literal[
        "COMPLETED",
        "SKIPPED_EMPTY_INPUT",
    ]

    schemaVersion: str
    calculationVersion: str

    entities: list[Entity]
    unresolvedNameLinks: list[dict]