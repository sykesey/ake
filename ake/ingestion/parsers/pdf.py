from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ake.ingestion.parsers.base import BaseParser


class PDFParser(BaseParser):
    def parse(self, path: Path) -> list[Any]:
        try:
            from unstructured.partition.pdf import partition_pdf
        except ImportError as exc:
            raise ImportError(
                "PDF parsing requires the 'ingestion' dependency group. "
                "Run: uv sync --group ingestion"
            ) from exc
        return partition_pdf(filename=str(path), strategy="fast")

    def parse_bytes(self, content: bytes, **kwargs: Any) -> list[Any]:
        try:
            from unstructured.partition.pdf import partition_pdf
        except ImportError as exc:
            raise ImportError(
                "PDF parsing requires the 'ingestion' dependency group. "
                "Run: uv sync --group ingestion"
            ) from exc
        return partition_pdf(file=io.BytesIO(content), strategy="fast")
