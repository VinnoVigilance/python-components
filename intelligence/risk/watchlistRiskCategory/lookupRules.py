"""Read risk labels asserted by canonical watchlist mapping."""

from intelligence.risk.labelContract import load_taxonomy


class RiskMappingError(ValueError):
    """A canonical risk mapping field failed validation."""

    def __init__(self, field, message):
        super().__init__(message)
        self.field = field


def record_rule_labels(record, taxonomy=None):
    """Return exact taxonomy labels and their mapped origin for one record."""
    risk_category = record.get("RiskCategory")
    if not isinstance(risk_category, dict):
        raise RiskMappingError("RiskCategory", "RiskCategory must be an object.")
    entries = risk_category.get("Labels")
    if not isinstance(entries, list):
        raise RiskMappingError("RiskCategory.Labels", "RiskCategory.Labels must be an array.")
    if taxonomy is None:
        _, taxonomy = load_taxonomy()
    valid_names = {item["display_name"] for item in taxonomy.values()}
    labels = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise RiskMappingError(f"RiskCategory.Labels[{index}]", f"RiskCategory.Labels[{index}] must be an object.")
        origin, name = entry.get("Type"), entry.get("Value")
        if origin not in ("List", "Record"):
            raise RiskMappingError(f"RiskCategory.Labels[{index}].Type", f"RiskCategory.Labels[{index}].Type must be List or Record.")
        if not isinstance(name, str) or name not in valid_names:
            raise RiskMappingError(f"RiskCategory.Labels[{index}].Value", f"RiskCategory.Labels[{index}].Value is not an exact taxonomy label: {name!r}.")
        labels.setdefault(name, f"mapped {origin.lower()} risk category")
    return labels
