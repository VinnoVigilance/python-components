"""Compatibility imports for the shared risk label contract."""

from intelligence.risk.labelContract import (
    RiskModelError as MediaRiskModelError,
    ModelLabelContract,
    _text,
    _read_json,
    _parse_taxonomy_rows,
    _read_workbook_taxonomy,
    load_label_contract,
)
