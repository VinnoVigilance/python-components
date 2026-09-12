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
        self.known_threshold = known_threshold

        self.consecutive_known_records = 0

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

        if is_known:
            self.consecutive_known_records += 1

        else:
            self.consecutive_known_records = 0

        should_stop = (
            self.consecutive_known_records
            >= self.known_threshold
        )

        return (
            is_known,
            should_stop,
        )

    def reset(
        self,
    ) -> None:
        self.consecutive_known_records = 0