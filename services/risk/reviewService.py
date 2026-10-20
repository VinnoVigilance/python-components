"""Persist human review separately from versioned machine assessments."""

from infrastructure.database.connection import get_db_connection
from intelligence.risk.labelContract import load_taxonomy
from repositories.risk import riskReviewRepository


def record_review(subject, assessment_id, label_id, verdict, reviewer,
                  evidence=None, comment=None, taxonomy_path=None):
    """Append one label verdict, including corrections for missed labels."""
    if subject not in {"media", "watchlist"}:
        raise ValueError("subject must be media or watchlist.")
    if verdict not in {"present", "absent", "uncertain"}:
        raise ValueError("verdict must be present, absent or uncertain.")
    if not isinstance(reviewer, str) or not reviewer.strip():
        raise ValueError("A reviewer identity is required.")
    taxonomy_version, labels = load_taxonomy(taxonomy_path)
    if label_id not in labels:
        raise ValueError("Unknown business label ID.")
    with get_db_connection() as connection:
        assessment = riskReviewRepository.find_assessment(connection, subject, assessment_id)
        if assessment is None:
            raise LookupError("Assessment not found.")
        metadata = assessment.get("metadata") or {}
        assessed_taxonomy = metadata.get("taxonomyVersion", assessment.get("taxonomyVersion"))
        if assessed_taxonomy != taxonomy_version:
            raise ValueError("Supply the taxonomy workbook snapshot used by this assessment.")
        with connection.cursor() as cursor:
            return riskReviewRepository.insert_review(
                cursor, subject, assessment_id, label_id, verdict, reviewer.strip(),
                taxonomy_version, evidence, comment,
            )


def list_reviews(label_id=None, verdict=None, after_id=0, limit=100,
                 subject=None, assessment_id=None):
    """Page through review events with their original machine assessments."""
    if limit < 1 or limit > 1000:
        raise ValueError("limit must be between 1 and 1000.")
    if subject is not None and subject not in {"media", "watchlist"}:
        raise ValueError("subject must be media or watchlist.")
    if assessment_id is not None and subject is None:
        raise ValueError("subject is required when filtering by assessment_id.")
    with get_db_connection() as connection:
        return riskReviewRepository.find_reviews(
            connection, label_id, verdict, after_id, limit, subject, assessment_id,
        )
