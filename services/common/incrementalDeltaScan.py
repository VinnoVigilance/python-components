"""Shared policy for destination-based incremental scans."""

from datetime import timedelta


def destination_delta_window(cursor, read_latest_date, through=None):
    """Return inclusive dates using the destination's latest insertion."""
    start_date = read_latest_date(cursor)
    if through is None:
        cursor.execute("SELECT CURRENT_DATE")
        through = cursor.fetchone()[0]
    return start_date, through


def iter_effective_dates(start_date, end_date):
    """Yield each calendar date from start through end, oldest first."""
    if start_date is None or end_date is None:
        return
    for offset in range((end_date - start_date).days + 1):
        yield start_date + timedelta(days=offset)
