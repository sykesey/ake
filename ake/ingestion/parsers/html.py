from __future__ import annotations

from pathlib import Path
from typing import Any

from ake.ingestion.parsers.base import BaseParser


class HtmlParser(BaseParser):
    def parse(self, path: Path) -> list[Any]:
        try:
            from unstructured.partition.html import partition_html
        except ImportError as exc:
            raise ImportError(
                "HTML parsing requires the 'ingestion' dependency group. "
                "Run: uv sync --group ingestion"
            ) from exc
        return partition_html(filename=str(path))

    def parse_bytes(self, content: bytes, **kwargs: Any) -> list[Any]:
        try:
            from unstructured.partition.html import partition_html
        except ImportError as exc:
            raise ImportError(
                "HTML parsing requires the 'ingestion' dependency group. "
                "Run: uv sync --group ingestion"
            ) from exc
        return partition_html(text=content.decode("utf-8", errors="replace"))
