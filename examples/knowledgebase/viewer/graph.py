"""Build a Cytoscape.js-compatible graph from ingestion results.

Nodes
-----
  document  — one per IngestionResult
  section   — one per unique section_path prefix (h1/h2/h3 headings)

Edges
-----
  document → h1 section
  h1 → h2 → h3  (parent → child section)

Element nodes are NOT included in the default graph; they are served
separately via /api/elements and rendered in the detail panel.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from ake.ingestion.pipeline import IngestionResult

# One accent colour per document (cycles if there are more than 5)
_DOC_COLOURS = ["#7c3aed", "#e11d48", "#0891b2", "#16a34a", "#ea580c"]

# Shared IT systems: name → {keywords (lower-case), colour}
# Keywords are matched case-insensitively against element text.
SHARED_SYSTEMS: dict[str, dict] = {
    "Microsoft 365": {
        "keywords": ["microsoft 365", "microsoft365", "microsoft teams", "sharepoint", "onedrive", "outlook"],
        "colour": "#0078d4",
    },
    "Slack": {
        "keywords": ["slack"],
        "colour": "#4a154b",
    },
    "Workday": {
        "keywords": ["workday"],
        "colour": "#f68220",
    },
    "GitHub": {
        "keywords": ["github"],
        "colour": "#24292f",
    },
    "Google Workspace": {
        "keywords": ["google workspace", "google drive", "google calendar", "gmail"],
        "colour": "#4285f4",
    },
    "PagerDuty": {
        "keywords": ["pagerduty"],
        "colour": "#25c151",
    },
}


def _doc_label(source_url: str) -> str:
    stem = Path(source_url.split("?")[0]).stem
    return stem.replace("-", " ").replace("_", " ").title()


def _section_id(short_id: str, path: tuple[str, ...]) -> str:
    return f"s:{short_id}:{'|'.join(path)}"


def _system_slug(name: str) -> str:
    return "sys:" + name.lower().replace(" ", "-")


def _detect_systems(text: str) -> list[str]:
    """Return names of shared systems mentioned in the given text."""
    lower = text.lower()
    return [
        name
        for name, cfg in SHARED_SYSTEMS.items()
        if any(kw in lower for kw in cfg["keywords"])
    ]


def build_graph(results: list[IngestionResult]) -> dict[str, Any]:
    """Return a dict with ``nodes``, ``edges``, and ``meta`` keys."""
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[str] = set()
    total_sections = 0

    # system_slug → set of section/document node IDs that reference it
    system_refs: dict[str, set[str]] = defaultdict(set)

    for i, result in enumerate(results):
        colour = _DOC_COLOURS[i % len(_DOC_COLOURS)]
        short_id = result.doc_id[:8]
        doc_nid = f"d:{short_id}"

        # ── Count elements per section path ─────────────────────────────────
        # total_counts[path] = Counter of types including all descendants
        total_counts: dict[tuple, Counter] = defaultdict(Counter)
        for el in result.elements:
            for depth in range(1, len(el.section_path) + 1):
                total_counts[tuple(el.section_path[:depth])][el.type] += 1

        # ── Document node ────────────────────────────────────────────────────
        nodes.append(
            {
                "data": {
                    "id": doc_nid,
                    "label": _doc_label(result.source_url),
                    "type": "document",
                    "doc_id": result.doc_id,
                    "source_url": result.source_url,
                    "element_count": len(result.elements),
                    "type_counts": dict(Counter(el.type for el in result.elements)),
                    "colour": colour,
                }
            }
        )

        # ── Section nodes ────────────────────────────────────────────────────
        all_paths = sorted(total_counts.keys(), key=lambda p: (len(p), p))
        total_sections += len(all_paths)

        # Collect system mentions per section path (leaf-level, not propagated up)
        section_systems: dict[tuple, set[str]] = defaultdict(set)
        for el in result.elements:
            for sys_name in _detect_systems(el.text):
                path_key = tuple(el.section_path) if el.section_path else ("(root)",)
                section_systems[path_key].add(sys_name)

        for path in all_paths:
            nid = _section_id(short_id, path)
            depth = len(path) - 1
            count = sum(total_counts[path].values())

            nodes.append(
                {
                    "data": {
                        "id": nid,
                        "label": path[-1],
                        "type": "section",
                        "depth": depth,
                        "section_path": list(path),
                        "doc_id": result.doc_id,
                        "element_count": count,
                        "type_counts": dict(total_counts[path]),
                        "colour": colour,
                        "systems": sorted(section_systems.get(path, set())),
                    }
                }
            )

            # Edge: parent → this section
            parent = doc_nid if depth == 0 else _section_id(short_id, path[:-1])
            eid = f"{parent}→{nid}"
            if eid not in seen_edges:
                edges.append(
                    {"data": {"id": eid, "source": parent, "target": nid, "colour": colour}}
                )
                seen_edges.add(eid)

            # Record which system nodes this section references
            for sys_name in section_systems.get(path, set()):
                system_refs[sys_name].add(nid)

        # Also check doc-level elements (section_path=[]) against the doc node
        for el in result.elements:
            if not el.section_path:
                for sys_name in _detect_systems(el.text):
                    system_refs[sys_name].add(doc_nid)

    # ── Shared system nodes ──────────────────────────────────────────────────
    # Only emit system nodes that are referenced by more than one document.
    # Single-document mentions are interesting but not cross-document links.
    for sys_name, referencing_nids in system_refs.items():
        referenced_docs = {nid.split(":")[1][:8] for nid in referencing_nids}
        if len(referenced_docs) < 2:
            continue

        slug = _system_slug(sys_name)
        cfg = SHARED_SYSTEMS[sys_name]
        nodes.append(
            {
                "data": {
                    "id": slug,
                    "label": sys_name,
                    "type": "system",
                    "colour": cfg["colour"],
                    "reference_count": len(referencing_nids),
                }
            }
        )

        for src_nid in referencing_nids:
            eid = f"{src_nid}→{slug}"
            if eid not in seen_edges:
                edges.append(
                    {
                        "data": {
                            "id": eid,
                            "source": src_nid,
                            "target": slug,
                            "colour": cfg["colour"],
                            "edge_type": "system-ref",
                        }
                    }
                )
                seen_edges.add(eid)

    system_count = sum(
        1 for n in nodes if n["data"].get("type") == "system"
    )

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "documents": len(results),
            "sections": total_sections,
            "total_elements": sum(len(r.elements) for r in results),
            "shared_systems": system_count,
        },
    }


def get_elements(
    results: list[IngestionResult],
    doc_id: str,
    section_path: list[str],
) -> list[dict]:
    """Return elements whose section_path starts with the given path."""
    result = next((r for r in results if r.doc_id == doc_id), None)
    if not result:
        return []
    return [
        {
            "element_id": el.element_id,
            "type": el.type,
            "text": el.text,
            "page": el.page,
            "section_path": el.section_path,
        }
        for el in result.elements
        if el.section_path[: len(section_path)] == section_path
    ]
