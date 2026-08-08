from .collector import collect_source
from .models import ApiCollectorTask

# Public front door: callers import both the action and the task shape from
# here, so they never need to know which internal module defines each.
__all__ = ["collect", "ApiCollectorTask"]


def collect(task: ApiCollectorTask) -> str:
    return collect_source(task)
