from .collector import (
    collect_artifacts,
    collect_profiles,
    collect_source,
)

from .models import ApiCollectionResult, ApiCollectorTask

__all__ = [
    "collect",
    "collect_artifacts",
    "ApiCollectionResult",
    "ApiCollectorTask",
    "collect_profiles",
]


def collect(task: ApiCollectorTask) -> str:
    return collect_source(task)