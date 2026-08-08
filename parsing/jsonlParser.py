"""Streaming JSON Lines parser."""

import json
from pathlib import Path


class JsonlParser:
    def parse(self, file_path, config=None):
        path = Path(file_path)
        with path.open(encoding="utf-8") as input_file:
            for line_number, line in enumerate(input_file, start=1):
                if not line.strip():
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as error:
                    raise ValueError(f"Invalid JSONL at {path}:{line_number}") from error
