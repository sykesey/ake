"""CSV parser — streams RecordBatches from a CSV file via pyarrow.csv.

Named ``csv_parser`` (not ``csv``) to avoid shadowing the stdlib ``csv`` module.
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

# Approx bytes per batch; pyarrow.csv block_size is byte-based, not row-based.
_DEFAULT_BLOCK_SIZE = 5 * 1024 * 1024  # 5 MB


class CsvParser:
    """Stream a CSV file as an iterator of :class:`pyarrow.RecordBatch` objects."""

    def _require_pyarrow(self) -> None:
        try:
            import pyarrow  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "CSV parsing requires pyarrow. Run: uv sync --group ingestion"
            ) from exc

    def _open_reader(self, path: Path, block_size: int) -> "pa.ipc.RecordBatchReader":
        import pyarrow.csv as pa_csv

        read_opts = pa_csv.ReadOptions(block_size=block_size)
        return pa_csv.open_csv(str(path), read_options=read_opts)

    def get_schema(self, path: Path) -> "pa.Schema":
        self._require_pyarrow()
        reader = self._open_reader(path, _DEFAULT_BLOCK_SIZE)
        # Read the first batch to resolve the inferred schema, then discard.
        batch = next(iter(reader))
        return batch.schema

    def schema_fingerprint(self, schema: "pa.Schema") -> str:
        buf = schema.serialize()
        return hashlib.sha256(buf.to_pybytes()).hexdigest()

    def partition_keys(self, path: Path) -> dict[str, str]:  # noqa: ARG002
        return {}

    def iter_batches(
        self, path: Path, block_size: int = _DEFAULT_BLOCK_SIZE
    ) -> Iterator["pa.RecordBatch"]:
        self._require_pyarrow()
        yield from self._open_reader(path, block_size)
