from typing import Any

from repositories.adverseMedia.mediaRepository import (
    exists_by_record_key,
)
from services.adverseMediaPipeline.mediaIdentityService import (
    MediaIdentityService,
)


class MediaDiscoveryService:
    """
    Handles media discovery decisions.

    Responsibilities:
    - Generate record_key through MediaIdentityService.
    - Check whether record_key already exists.
    - Track consecutive known records.
    - Decide when discovery should stop.
    """

    def __init__(
        self,
        cursor,
        source_id: int,
        dataset_id: int,
        source_config: dict[str, Any],
        stop_after_known: bool,
        known_threshold: int = 10,
    ):
        if known_threshold <= 0:
            raise ValueError(
                "known_threshold must be greater than zero."
            )

        self.cursor = cursor
        self.source_id = source_id
        self.dataset_id = dataset_id
        self.source_config = source_config
        self.stop_after_known = stop_after_known
        self.known_threshold = known_threshold

        self.consecutive_known_records = 0
        self.discovered_count = 0
        self.known_count = 0
        self.new_count = 0
        self.identity_failure_count = 0
        self.discovery_failure_count = 0
        self.selected_detail_count = 0
        self.reached_source_end = False
        self.stop_reason: str | None = None

    def build_record_key(
        self,
        record: dict[str, Any],
    ) -> str:
        """
        Build the deterministic VV record key.
        """

        return MediaIdentityService.generate_record_key(
            source_config=self.source_config,
            record=record,
        )

    def check_record_key(
        self,
        record_key: str,
    ) -> tuple[bool, bool]:
        """
        Check whether record_key already exists.

        Returns:
            is_known,
            should_stop
        """

        is_known = exists_by_record_key(
            cursor=self.cursor,
            source_id=self.source_id,
            dataset_id=self.dataset_id,
            record_key=record_key,
        )

        self.discovered_count += 1

        if is_known:
            self.consecutive_known_records += 1
            self.known_count += 1

        else:
            self.consecutive_known_records = 0
            self.new_count += 1

        should_stop = (
            self.stop_after_known
            and self.consecutive_known_records
            >= self.known_threshold
        )

        if should_stop:
            self.stop_reason = (
                "KNOWN_THRESHOLD_REACHED"
            )

        return (
            is_known,
            should_stop,
        )

    def record_detail_selected(
        self,
    ) -> None:
        """Count a detail page that the crawler is expected to return."""

        self.selected_detail_count += 1

    def record_identity_failure(
        self,
    ) -> None:
        """Record an unknown candidate and break the known streak.

        A missing identity or a failed database lookup cannot safely count as
        part of a consecutive-known boundary.
        """

        self.discovered_count += 1
        self.identity_failure_count += 1
        self.discovery_failure_count += 1
        self.consecutive_known_records = 0

    def mark_discovery_failure(
        self,
        reason: str,
    ) -> None:
        """Mark a source-level discovery failure such as an empty first page."""

        self.discovery_failure_count += 1
        self.consecutive_known_records = 0
        self.stop_reason = reason

    def mark_source_end(
        self,
    ) -> None:
        """Mark that discovery reached the natural end of the source."""

        self.reached_source_end = True

        if self.stop_reason is None:
            self.stop_reason = "SOURCE_EXHAUSTED"

    def get_summary(
        self,
    ) -> dict[str, Any]:
        """Return explicit discovery completeness information."""

        completed_safely = (
            self.discovery_failure_count == 0
            and (
                self.reached_source_end
                or self.stop_reason
                == "KNOWN_THRESHOLD_REACHED"
            )
        )

        return {
            "discovered_count": self.discovered_count,
            "known_count": self.known_count,
            "new_count": self.new_count,
            "identity_failure_count": (
                self.identity_failure_count
            ),
            "discovery_failure_count": (
                self.discovery_failure_count
            ),
            "selected_detail_count": (
                self.selected_detail_count
            ),
            "reached_source_end": (
                self.reached_source_end
            ),
            "stop_reason": (
                self.stop_reason
                or "UNEXPECTED_STOP"
            ),
            "completed_safely": completed_safely,
        }

    def reset(
        self,
    ) -> None:
        self.consecutive_known_records = 0
