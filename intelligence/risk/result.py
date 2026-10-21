"""Shared risk assessment payload helpers."""

import hashlib
from pathlib import Path


def implementation_version(paths):
    """Fingerprint runtime sources that can change an assessment."""
    digest = hashlib.sha256()
    for path in sorted((Path(item) for item in paths), key=lambda item: item.as_posix()):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return "sha256:" + digest.hexdigest()


def calculation_version(*components):
    """Identify the exact model assets and decision implementation together."""
    digest = hashlib.sha256()
    for component in components:
        digest.update(component.encode("utf-8"))
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


def build_assessment_payload(payload):
    """Add the schema version without duplicating database columns."""
    return {**payload, "schemaVersion": "2.0"}
