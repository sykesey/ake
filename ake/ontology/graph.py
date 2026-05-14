"""Build a persistable graph and element tree from ontology / ingestion results.

:func:`build_graph` — Cytoscape.js-compatible JSON graph (nodes + edges).
  Nodes: dataset root, one per table class.
  Edges: containment (dataset→table) and FK object properties (table→table).

:func:`build_element_tree` — hierarchical JSON tree of all ingested elements,
  grouped by table, with parsed field dicts for direct inspection.
"""
from __future__ import annotations

from typing import Any

from ake.ingestion.amorphous_pipeline import AmorphousIngestionResult
from ake.ontology.model import Ontology

_TABLE_COLOURS = [
    "#0891b2",  # cyan
    "#7c3aed",  # violet
    "#16a34a",  # green
    "#e11d48",  # rose
    "#ea580c",  # orange
    "#d97706",  # amber
    "#0d9488",  # teal
    "#9333ea",  # purple
]

_DATASET_COLOUR = "#334155"
_FK_COLOUR = "#94a3b8"
_DOC_COLOUR = "#64748b"


def _parse_row_text(text: str) -> dict[str, str]:
    row: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            col, _, val = line.partition(": ")
            row[col.strip()] = val.strip()
    return row


def build_graph(ontology: Ontology, result: AmorphousIngestionResult) -> dict[str, Any]:
    """Return a Cytoscape.js-compatible graph dict with ``nodes``, ``edges``, and ``meta``."""
    nodes: list[dict] = []
    edges: list[dict] = []

    dataset_id = f"dataset:{ontology.dataset_name}"
    total_elements = sum(t.row_count for t in result.tables) + sum(
        d.element_count for d in result.documents
    )

    nodes.append({
        "data": {
            "id": dataset_id,
            "label": ontology.dataset_name,
            "type": "dataset",
            "colour": _DATASET_COLOUR,
            "element_count": total_elements,
        }
    })

    colour_by_table: dict[str, str] = {}
    for i, cls in enumerate(ontology.classes):
        colour = _TABLE_COLOURS[i % len(_TABLE_COLOURS)]
        colour_by_table[cls.table] = colour
        node_id = f"table:{cls.table}"

        nodes.append({
            "data": {
                "id": node_id,
                "label": cls.label,
                "type": "table",
                "table": cls.table,
                "doc_id": cls.doc_id,
                "colour": colour,
                "row_count": cls.row_count,
                "class_name": cls.name,
                "property_count": len(cls.properties),
            }
        })

        edges.append({
            "data": {
                "id": f"contains:{cls.table}",
                "source": dataset_id,
                "target": node_id,
                "type": "contains",
                "colour": colour,
            }
        })

    seen_fk_edges: set[str] = set()
    for rel in ontology.relationships:
        eid = f"fk:{rel.source_table}.{rel.source_column}→{rel.target_table}"
        if eid in seen_fk_edges:
            continue
        seen_fk_edges.add(eid)
        edges.append({
            "data": {
                "id": eid,
                "source": f"table:{rel.source_table}",
                "target": f"table:{rel.target_table}",
                "type": "foreign_key",
                "colour": _FK_COLOUR,
                "label": rel.source_column,
                "confidence": rel.confidence,
                "evidence": rel.evidence,
            }
        })

    doc_links_by_name = {lk.document_name: lk for lk in result.document_links}
    for doc in result.documents:
        lk = doc_links_by_name.get(doc.name)
        label = f"{lk.entity_id}\n{lk.doc_type}" if lk else doc.name.replace("_", " ")
        nodes.append({
            "data": {
                "id": f"doc:{doc.name}",
                "label": label,
                "type": "document",
                "colour": _DOC_COLOUR,
                "doc_name": doc.name,
                "element_count": doc.element_count,
                "doc_type": lk.doc_type if lk else "document",
                "linked_table": lk.table_name if lk else None,
                "entity_id": lk.entity_id if lk else None,
            }
        })
        if lk:
            edges.append({
                "data": {
                    "id": f"doclink:{doc.name}",
                    "source": f"doc:{doc.name}",
                    "target": f"table:{lk.table_name}",
                    "type": "document_link",
                    "colour": _DOC_COLOUR,
                    "label": lk.entity_id,
                    "doc_type": lk.doc_type,
                }
            })

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "dataset": ontology.dataset_name,
            "namespace": ontology.namespace,
            "generated_at": ontology.generated_at,
            "tables": len(ontology.classes),
            "documents": len(result.documents),
            "total_elements": total_elements,
            "fk_relationships": len(ontology.relationships),
        },
    }


def build_element_tree(result: AmorphousIngestionResult) -> dict[str, Any]:
    """Return a hierarchical JSON tree of all ingested elements.

    Structure::

        {
          "dataset": "...",
          "tables": {
            "employees": {
              "doc_id": "...",
              "row_count": 15,
              "rows": [{"element_id": ..., "fields": {...}, "normalized_values": {...}}, ...]
            }
          },
          "documents": {
            "report": {"doc_id": ..., "element_count": 12, "elements": [...]}
          }
        }
    """
    tables_tree: dict[str, Any] = {}
    for tbl in result.tables:
        rows = []
        for el in tbl.result.elements:
            rows.append({
                "element_id": el.element_id,
                "row_id": el.metadata.get("row_id", ""),
                "section_path": el.section_path,
                "fields": _parse_row_text(el.text),
                "normalized_values": el.metadata.get("normalized_values", {}),
            })
        tables_tree[tbl.name] = {
            "doc_id": tbl.result.doc_id,
            "row_count": tbl.row_count,
            "rows": rows,
        }

    documents_tree: dict[str, Any] = {}
    for doc in result.documents:
        documents_tree[doc.name] = {
            "doc_id": doc.result.doc_id,
            "element_count": doc.element_count,
            "elements": [
                {
                    "element_id": el.element_id,
                    "type": el.type,
                    "section_path": el.section_path,
                    "text": el.text[:500],
                }
                for el in doc.result.elements
            ],
        }

    return {
        "dataset": result.dataset_name,
        "source_dir": str(result.source_dir),
        "tables": tables_tree,
        "documents": documents_tree,
    }
