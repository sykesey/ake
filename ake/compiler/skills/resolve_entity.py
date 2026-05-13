"""Map an entity mention to a canonical ID via fuzzy matching.

No LLM calls — deterministic case-insensitive prefix/substring matching
against a registry dictionary.
"""
from __future__ import annotations

from typing import Optional


def resolve_entity(
    name: str,
    entity_registry: dict[str, str],
    *,
    case_sensitive: bool = False,
) -> Optional[str]:
    """Return the canonical entity ID for *name*, or None.

    The registry is a ``{display_name: canonical_id}`` dict.  Matching is
    case-insensitive by default and uses the longest-prefix strategy:

    1. Exact match on the full name (after normalising case).
    2. The name appears as a case-insensitive substring of a registry key.
    3. A registry key appears as a case-insensitive substring of *name*.

    When multiple registry keys match, the longest key wins.

    Args:
        name:             Human-written entity mention, e.g. ``"Nvidia Corp."``.
        entity_registry:  ``{display_name: canonical_id}`` mapping.
        case_sensitive:   If True, matching is case-sensitive.

    Returns:
        The canonical ID string, or *None* if no match is found.
    """
    if not name or not entity_registry:
        return None

    search = name.strip() if case_sensitive else name.strip().lower()

    # Build a case-normalised lookup.
    lookup: dict[str, str] = {}
    for display, cid in entity_registry.items():
        key = display.strip() if case_sensitive else display.strip().lower()
        lookup[key] = cid

    # 1. Exact match.
    if search in lookup:
        return lookup[search]

    # 2–3. Substring / containment match — pick longest matching key.
    best_key: str | None = None
    best_len = 0

    for key, cid in lookup.items():
        if search in key:
            if len(key) > best_len:
                best_key = key
                best_len = len(key)
        elif key in search:
            if len(key) > best_len:
                best_key = key
                best_len = len(key)

    if best_key is not None:
        return lookup[best_key]

    return None