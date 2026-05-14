"""Arrow IPC parser — streams RecordBatches from Arrow file/stream format.

- ``.arrow`` / ``.feather`` → Arrow IPC file format (random-access)
- ``.arrows``              → Arrow IPC stream format (sequential)
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

_STREAM_SUFFIXES: frozenset[str] = frozenset({".arrows"})


class ArrowIPCParser:
    """Stream an Arrow IPC file as an iterator of :class:`pyarrow.RecordBatch` objects."""

    def _require_pyarrow(self) -> None:
        try:
            import pyarrow  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Arrow IPC parsing requires pyarrow. Run: uv sync --group ingestion"
            ) from exc

    def _open_reader(self, path: Path) -> "pa.ipc.RecordBatchReader":
        import pyarrow.ipc as pa_ipc

        if path.suffix.lower() in _STREAM_SUFFIXES:
            return pa_ipc.open_stream(str(path))
        return pa_ipc.open_file(str(path))

    def get_schema(self, path: Path) -> "pa.Schema":
        self._require_pyarrow()
        return self._open_reader(path).schema_arrow

    def schema_fingerprint(self, schema: "pa.Schema") -> str:
        buf = schema.serialize()
        return hashlib.sha256(buf.to_pybytes()).hexdigest()

    def partition_keys(self, path: Path) -> dict[str, str]:  # noqa: ARG002
        return {}

    def iter_batches(self, path: Path) -> Iterator["pa.RecordBatch"]:
        self._require_pyarrow()
        yield from self._open_reader(path)
