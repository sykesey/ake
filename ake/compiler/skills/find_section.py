"""Retrieve elements matching a ``section_path`` prefix.

No LLM calls — deterministic prefix matching on the section_path list.
"""
from __future__ import annotations

from ake.ingestion.element import Element


def find_section(
    elements: list[Element],
    path: list[str],
) -> list[Element]:
    """Return every element whose ``section_path`` starts with *path*.

    Args:
        elements: Normalised Element records.
        path:     Prefix to match, e.g. ``["Item 7", "Capital Returns"]``.

    Returns:
        Elements in the matching section, preserving input order.  An empty
        list if no element's section_path starts with *path*.
    """
    n = len(path)
    if n == 0:
        return list(elements)

    result: list[Element] = []
    for el in elements:
        if len(el.section_path) >= n and el.section_path[:n] == path:
            result.append(el)
    return result