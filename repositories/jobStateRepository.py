"""JSON state files for job work that must survive between runs."""

import csv
import json
import os
from pathlib import Path
from typing import Any


STATE_DIR = Path(__file__).resolve().parents[1] / "data" / "state"


def _state_path(kind: str, name: str) -> Path:
    return STATE_DIR / kind / f"{name}.json"


def load_state(kind: str, name: str) -> dict[str, Any] | None:
    """Return the saved state for one source, or None when there is none."""

    path = _state_path(kind, name)

    if not path.is_file():
        return None

    return json.loads(path.read_text(encoding="utf-8"))


def save_state(kind: str, name: str, state: dict[str, Any]) -> Path:
    """Write the state through a temporary file so it is never half-written."""

    path = _state_path(kind, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
    return path


def delete_state(kind: str, name: str) -> None:
    _state_path(kind, name).unlink(missing_ok=True)


def export_pending_csv(kind: str, path: Path) -> int:
    """Write every pending item of one kind to a CSV; return the number of rows."""

    rows = []

    for name in list_state_names(kind):
        state = load_state(kind, name) or {}
        items = state.get("missing") or list(state.get("items", {}).values())

        for item in items:
            rows.append({
                "source": name,
                "key": item.get("record_id") or item.get("record_key"),
                "url": item.get("detail_url") or item.get("detail_file_path"),
                "attempts": item.get("attempts", state.get("attempts")),
                "error": item.get("error"),
                "broken_on_source": bool(item.get("permanent")),
            })

    if rows:
        with Path(path).open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    return len(rows)


def list_state_names(kind: str) -> list[str]:
    """Return the sources that currently have saved state."""

    folder = STATE_DIR / kind

    if not folder.is_dir():
        return []

    return sorted(path.stem for path in folder.glob("*.json"))
