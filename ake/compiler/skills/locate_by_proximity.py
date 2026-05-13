"""Find elements near a known anchor phrase.

No LLM calls — deterministic element-neighbourhood scanning.
"""
from __future__ import annotations

from ake.ingestion.element import Element


def locate_by_proximity(
    elements: list[Element],
    anchor_text: str,
    window: int = 3,
    *,
    case_sensitive: bool = False,
) -> list[Element]:
    """Return elements within ±*window* of any element containing *anchor_text*.

    Args:
        elements:       Normalised Element records.
        anchor_text:    Phrase to search for (substring match by default).
        window:         Number of elements before and after the anchor to return.
        case_sensitive: If True, the anchor_text must match case exactly.

    Returns:
        Neighbouring elements in document order, de-duplicated when multiple
        anchors overlap.  An empty list if *anchor_text* is not found.
    """
    search = anchor_text if case_sensitive else anchor_text.lower()
    n = len(elements)

    # Collect unique indices within the window.
    seen: set[int] = set()
    for i, el in enumerate(elements):
        candidate = el.text if case_sensitive else el.text.lower()
        if search in candidate:
            start = max(0, i - window)
            end = min(n, i + window + 1)
            seen.update(range(start, end))

    return [elements[i] for i in sorted(seen)]