"""Calendar and interval-based eligibility for the watchlist job."""

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from infrastructure.database.connection import connection_pool
from repositories import watchlistFileRepository, watchlistJobRepository


_INTERVAL = re.compile(r"every_([1-9][0-9]*)_(days|weeks|months|hours)\Z")


@dataclass(frozen=True)
class RunDecision:
    should_run: bool
    reason: str
    last_successful_check: datetime | None


def get_last_successful_check(config: dict) -> datetime | None:
    """Resolve this exact source/list and read its last successful check."""
    connection = connection_pool.getconn()
    try:
        with connection:
            with connection.cursor() as cursor:
                source_name = config["source_name"]
                list_name = config["list_name"]
                source_id = watchlistFileRepository.find_source_id(cursor, source_name)
                if source_id is None:
                    raise LookupError(f"Source lookup missing: {source_name}")
                list_type_id = watchlistFileRepository.find_list_type_id(
                    cursor, source_id, list_name
                )
                if list_type_id is None:
                    raise LookupError(
                        f"List type lookup missing: {source_name}/{list_name}"
                    )
                return watchlistJobRepository.find_last_successful_check(
                    cursor, source_id, list_type_id
                )
    finally:
        connection_pool.putconn(connection)


def _add_months(day: date, number: int) -> date:
    """Advance calendar months, clamping the day at the end of the month."""
    month_index = day.year * 12 + day.month - 1 + number
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    last_day = (next_month - timedelta(days=1)).day
    return date(year, month, min(day.day, last_day))


def decide_run(
    schedule: str,
    last_successful_check: datetime | None,
    now: datetime,
    timezone_name: str | None,
) -> RunDecision:
    """Compute eligibility; calendar periods differ from rolling intervals.

    daily/weekly/monthly/hourly permit one successful check per local calendar
    period. every_N_days/weeks/months use local calendar dates, while
    every_N_hours uses an exact elapsed duration in UTC.
    """
    if not schedule or not isinstance(schedule, str):
        raise ValueError("Watchlist schedule is missing or invalid")
    rule = schedule.strip().lower()
    interval = _INTERVAL.fullmatch(rule)
    if rule not in {"daily", "weekly", "monthly", "hourly"} and not interval:
        raise ValueError(f"Unsupported watchlist schedule: {schedule!r}")
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be a timezone-aware datetime")
    tz = ZoneInfo(timezone_name) if timezone_name else None
    local_now = now.astimezone(tz)
    if last_successful_check is None:
        return RunDecision(True, "no previous successful check", None)
    if (
        last_successful_check.tzinfo is None
        or last_successful_check.utcoffset() is None
    ):
        raise ValueError("Database check time must be timezone-aware")
    local_last = last_successful_check.astimezone(tz)
    if last_successful_check > now:
        raise ValueError("Last successful check is in the future; verify clocks")

    if rule == "daily":
        due = local_now.date() > local_last.date()
    elif rule == "weekly":
        due = local_now.date().isocalendar()[:2] != local_last.date().isocalendar()[:2]
    elif rule == "monthly":
        due = (local_now.year, local_now.month) != (local_last.year, local_last.month)
    elif rule == "hourly":
        # Compare wall-clock hour labels, including when the OS changes UTC offset.
        due = (local_now.year, local_now.month, local_now.day, local_now.hour) != (
            local_last.year, local_last.month, local_last.day, local_last.hour
        )
    else:
        count, unit = int(interval.group(1)), interval.group(2)
        if unit == "days":
            due = local_now.date() >= local_last.date() + timedelta(days=count)
        elif unit == "weeks":
            due = local_now.date() >= local_last.date() + timedelta(weeks=count)
        elif unit == "months":
            due = local_now.date() >= _add_months(local_last.date(), count)
        else:
            due = now.astimezone(timezone.utc) >= (
                last_successful_check.astimezone(timezone.utc)
                + timedelta(hours=count)
            )

    return RunDecision(
        due,
        "schedule due" if due else "already checked within schedule",
        last_successful_check,
    )


def should_run_today(config: dict, now: datetime, timezone_name: str | None) -> RunDecision:
    """The job's single entry point for schedule and database checks."""
    schedule = config.get("schedule")
    # Validate before opening a database connection for an invalid config.
    decide_run(schedule, None, now, timezone_name)
    last_check = get_last_successful_check(config)
    return decide_run(schedule, last_check, now, timezone_name)