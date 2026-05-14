"""Parquet parser — streams RecordBatches from a single Parquet file.

Partition key-values in Hive-style path segments (key=value) are extracted
from the file path and returned via :meth:`partition_keys`.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import TYPE_CHECKING, Iterator

if TYPE_CHECKING:
    import pyarrow as pa

_DEFAULT_BATCH_SIZE = 10_000
_HIVE_PART_RE = re.compile(r"^([^=]+)=(.+)$")


def _extract_hive_partitions(path: Path) -> dict[str, str]:
    parts: dict[str, str] = {}
    for segment in path.parts:
        m = _HIVE_PART_RE.match(segment)
        if m:
            parts[m.group(1)] = m.group(2)
    return parts


class ParquetParser:
    """Stream a Parquet file as an iterator of :class:`pyarrow.RecordBatch` objects.

    Never loads the full dataset into memory; uses ``ParquetFile.iter_batches()``.
    """

    def _require_pyarrow(self) -> None:
        try:
            import pyarrow  # noqa: F401
        except ImportError as exc:
            raise ImportError(
                "Parquet parsing requires pyarrow. Run: uv sync --group ingestion"
            ) from exc

    def get_schema(self, path: Path) -> "pa.Schema":
        self._require_pyarrow()
        import pyarrow.parquet as pq

        return pq.read_schema(str(path))

    def schema_fingerprint(self, schema: "pa.Schema") -> str:
        """Stable hash of the schema's IPC serialisation (column names + types)."""
        buf = schema.serialize()
        return hashlib.sha256(buf.to_pybytes()).hexdigest()

    def partition_keys(self, path: Path) -> dict[str, str]:
        """Extract Hive-style partition key-value pairs from the file path."""
        return _extract_hive_partitions(path)

    def iter_batches(
        self, path: Path, batch_size: int = _DEFAULT_BATCH_SIZE
    ) -> Iterator["pa.RecordBatch"]:
        self._require_pyarrow()
        import pyarrow.parquet as pq

        pf = pq.ParquetFile(str(path))
        yield from pf.iter_batches(batch_size=batch_size)
