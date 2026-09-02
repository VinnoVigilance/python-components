from .collector import collect_source, plan_source
from .models import ApiCollectorTask

# Public front door: callers import both the action and the task shape from
# here, so they never need to know which internal module defines each.
__all__ = ["collect", "plan", "ApiCollectorTask"]


def collect(task: ApiCollectorTask) -> str:
    return collect_source(task)


def plan(task: ApiCollectorTask):
    """Dry-run the fan-out planner (no records fetched). See plan_source."""
    return plan_source(task)
