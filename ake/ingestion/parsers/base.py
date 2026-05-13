from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any


class BaseParser(ABC):
    @abstractmethod
    def parse(self, path: Path) -> list[Any]:
        """Parse a file on disk; return raw unstructured element objects."""

    @abstractmethod
    def parse_bytes(self, content: bytes, **kwargs: Any) -> list[Any]:
        """Parse in-memory content; return raw unstructured element objects."""
