from .collector import collect_artifacts, collect_source
from .models import ApiCollectionResult, ApiCollectorTask

__all__ = [
    "collect",
    "collect_artifacts",
    "ApiCollectionResult",
    "ApiCollectorTask",
]


def collect(task: ApiCollectorTask) -> str:
    return collect_source(task)