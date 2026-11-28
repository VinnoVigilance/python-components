"""Small disk-backed sequence for large crawler record collections."""

import json
from collections.abc import MutableSequence
from pathlib import Path
from typing import Any, Iterator


class JsonlRecordBuffer(MutableSequence):
    """Store JSON records on disk while keeping only byte offsets in memory."""

    def __init__(self, path: str | Path, *, reset: bool = False):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if reset:
            self.path.write_text("", encoding="utf-8")
        elif not self.path.exists():
            self.path.touch()
        self._offsets: list[int] = []
        self._reindex()
        self._writer = self.path.open("ab", buffering=0)

    def _reindex(self) -> None:
        self._offsets.clear()
        with self.path.open("rb") as stream:
            while True:
                offset = stream.tell()
                line = stream.readline()
                if not line:
                    break
                if line.strip():
                    self._offsets.append(offset)

    def __len__(self) -> int:
        return len(self._offsets)

    def __iter__(self) -> Iterator[dict[str, Any]]:
        self._writer.flush()
        with self.path.open("r", encoding="utf-8") as stream:
            for line in stream:
                if line.strip():
                    yield json.loads(line)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return list(self)[index]
        if index < 0:
            index += len(self)
        self._writer.flush()
        with self.path.open("rb") as stream:
            stream.seek(self._offsets[index])
            return json.loads(stream.readline().decode("utf-8"))

    def append(self, value: dict[str, Any]) -> None:
        offset = self._writer.tell()
        line = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
        self._writer.write(line.encode("utf-8"))
        self._writer.flush()
        self._offsets.append(offset)

    def insert(self, index: int, value: dict[str, Any]) -> None:
        values = list(self)
        values.insert(index, value)
        self._rewrite(values)

    def __setitem__(self, index, value) -> None:
        values = list(self)
        values[index] = value
        self._rewrite(values)

    def __delitem__(self, index) -> None:
        values = list(self)
        del values[index]
        self._rewrite(values)

    def _rewrite(self, values) -> None:
        self.close()
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        with temporary.open("wb") as stream:
            for value in values:
                line = json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n"
                stream.write(line.encode("utf-8"))
        temporary.replace(self.path)
        self._reindex()
        self._writer = self.path.open("ab", buffering=0)

    def close(self) -> None:
        writer = getattr(self, "_writer", None)
        if writer is not None and not writer.closed:
            writer.flush()
            writer.close()

