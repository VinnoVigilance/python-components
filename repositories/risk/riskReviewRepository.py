"""Append human label decisions and query their assessment provenance."""

from psycopg2.extras import RealDictCursor


def find_assessment(connection, subject, assessment_id):
    if subject not in {"media", "watchlist"}:
        raise ValueError("subject must be media or watchlist.")
    query = ("SELECT risk_details FROM core.media_risk_category WHERE id = %s"
             if subject == "media" else
             "SELECT risk_details FROM core.member_risk_category WHERE id = %s")
    with connection.cursor() as cursor:
        cursor.execute(query, (assessment_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def insert_review(cursor, subject, assessment_id, label_id, verdict, reviewer,
                  taxonomy_version, evidence=None, comment=None):
    media_id = assessment_id if subject == "media" else None
    member_id = assessment_id if subject == "watchlist" else None
    cursor.execute("""
        INSERT INTO core.risk_label_review (
            media_risk_category_id, member_risk_category_id, risk_category_id,
            verdict, reviewed_by, taxonomy_version, evidence, comment
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) RETURNING id
    """, (media_id, member_id, label_id, verdict, reviewer, taxonomy_version, evidence, comment))
    return cursor.fetchone()[0]


def find_reviews(connection, label_id=None, verdict=None, after_id=0, limit=100,
                 subject=None, assessment_id=None):
    with connection.cursor(cursor_factory=RealDictCursor) as cursor:
        cursor.execute("""
            SELECT review.*, COALESCE(media.risk_details, member.risk_details) AS machine_assessment,
                   media.media_record_id, member.watchlist_member_id
            FROM core.risk_label_review AS review
            LEFT JOIN core.media_risk_category AS media ON media.id = review.media_risk_category_id
            LEFT JOIN core.member_risk_category AS member ON member.id = review.member_risk_category_id
            WHERE review.id > %(after)s
              AND (%(label)s IS NULL OR review.risk_category_id = %(label)s)
              AND (%(verdict)s IS NULL OR review.verdict = %(verdict)s)
              AND (%(subject)s IS NULL OR
                   (%(subject)s = 'media' AND review.media_risk_category_id IS NOT NULL) OR
                   (%(subject)s = 'watchlist' AND review.member_risk_category_id IS NOT NULL))
              AND (%(assessment)s IS NULL OR
                   (%(subject)s = 'media' AND review.media_risk_category_id = %(assessment)s) OR
                   (%(subject)s = 'watchlist' AND review.member_risk_category_id = %(assessment)s))
            ORDER BY review.id LIMIT %(limit)s
        """, {"after": after_id, "label": label_id, "verdict": verdict, "limit": limit,
              "subject": subject, "assessment": assessment_id})
        return [dict(row) for row in cursor.fetchall()]
