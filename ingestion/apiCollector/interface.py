from .collector import collect_source
from .models import ApiCollectorTask

__all__ = ["collect", "ApiCollectorTask"]


def collect(task: ApiCollectorTask) -> str:
    return collect_source(task)
