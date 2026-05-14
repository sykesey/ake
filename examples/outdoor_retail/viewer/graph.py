"""Build a Cytoscape.js-compatible graph from tabular ingestion results.

Graph structure
---------------
  dataset    — root node representing the whole business data lake
  table      — one node per ingested CSV table
  group      — category groupings within each table
                 products → grouped by category
                 employees → grouped by department
                 locations → grouped by region
                 inventory → grouped by location_id
                 sales     → grouped by location_id
  cross-edge — dashed edges linking tables that share a foreign key value
                 inventory.location_id   → locations
                 inventory.product_id    → products
                 sales.location_id       → locations
                 sales.product_id        → products
                 sales.employee_id       → employees
                 employees.location_id   → locations
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from ake.ingestion.pipeline import IngestionResult

# One accent colour per table (cycles for extra tables)
_TABLE_COLOURS: dict[str, str] = {
    "locations": "#0891b2",   # cyan
    "employees": "#7c3aed",   # violet
    "products":  "#16a34a",   # green
    "inventory": "#e11d48",   # rose
    "sales":     "#ea580c",   # orange
}
_DEFAULT_COLOUR = "#475569"

# Which column to use for within-table grouping
_GROUP_COLUMN: dict[str, str] = {
    "locations": "region",
    "employees": "department",
    "products":  "category",
    "inventory": "location_id",
    "sales":     "location_id",
}

# Foreign-key relationships: (source_table, fk_column) → target_table
_FK_EDGES: list[tuple[str, str, str]] = [
    ("employees", "location_id", "locations"),
    ("inventory", "location_id", "locations"),
    ("inventory", "product_id",  "products"),
    ("sales",     "location_id", "locations"),
    ("sales",     "product_id",  "products"),
    ("sales",     "employee_id", "employees"),
]


def _table_name(result: IngestionResult) -> str:
    els = result.elements
    if els:
        return els[0].metadata.get("table", result.doc_id[:8])
    # Fall back to deriving from the section_path
    if els and len(els[0].section_path) > 1:
        return els[0].section_path[1]
    return result.doc_id[:8]


def _parse_row(text: str) -> dict[str, str]:
    row: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            col, _, val = line.partition(": ")
            row[col.strip()] = val.strip()
    return row


def build_graph(results: list[IngestionResult]) -> dict[str, Any]:
    """Return a dict with ``nodes``, ``edges``, and ``meta`` keys."""
    nodes: list[dict] = []
    edges: list[dict] = []
    seen_edges: set[str] = set()

    # dataset root node
    nodes.append({
        "data": {
            "id": "dataset:summit_trail",
            "label": "Summit & Trail",
            "type": "dataset",
            "colour": "#475569",
            "element_count": sum(len(r.elements) for r in results),
        }
    })

    # Build table-level info
    table_nodes: dict[str, str] = {}   # table_name → node id
    table_doc_ids: dict[str, str] = {}  # table_name → doc_id
    group_nodes: dict[tuple[str, str], str] = {}  # (table_name, group_val) → node id

    for result in results:
        tbl = _table_name(result)
        colour = _TABLE_COLOURS.get(tbl, _DEFAULT_COLOUR)
        tbl_nid = f"table:{tbl}"
        table_nodes[tbl] = tbl_nid
        table_doc_ids[tbl] = result.doc_id

        # Count groups within this table
        group_col = _GROUP_COLUMN.get(tbl)
        group_counts: Counter = Counter()
        for el in result.elements:
            row = _parse_row(el.text)
            val = row.get(group_col, "(other)") if group_col else "(all)"
            group_counts[val] += 1

        nodes.append({
            "data": {
                "id": tbl_nid,
                "label": tbl.replace("_", " ").title(),
                "type": "table",
                "table": tbl,
                "doc_id": result.doc_id,
                "colour": colour,
                "element_count": len(result.elements),
                "group_column": group_col or "",
                "type_counts": dict(group_counts),
            }
        })

        # Edge: dataset → table
        eid = f"dataset:summit_trail→{tbl_nid}"
        edges.append({"data": {"id": eid, "source": "dataset:summit_trail", "target": tbl_nid, "colour": colour}})
        seen_edges.add(eid)

        # Group nodes
        for group_val, row_count in group_counts.items():
            g_nid = f"group:{tbl}:{group_val}"
            group_nodes[(tbl, group_val)] = g_nid
            nodes.append({
                "data": {
                    "id": g_nid,
                    "label": group_val,
                    "type": "group",
                    "table": tbl,
                    "group_value": group_val,
                    "doc_id": result.doc_id,
                    "colour": colour,
                    "element_count": row_count,
                }
            })
            # Edge: table → group
            ge = f"{tbl_nid}→{g_nid}"
            if ge not in seen_edges:
                edges.append({"data": {"id": ge, "source": tbl_nid, "target": g_nid, "colour": colour}})
                seen_edges.add(ge)

    # Cross-table FK edges
    for src_tbl, fk_col, tgt_tbl in _FK_EDGES:
        src_nid = table_nodes.get(src_tbl)
        tgt_nid = table_nodes.get(tgt_tbl)
        if not src_nid or not tgt_nid:
            continue
        eid = f"fk:{src_tbl}.{fk_col}→{tgt_tbl}"
        if eid not in seen_edges:
            edges.append({
                "data": {
                    "id": eid,
                    "source": src_nid,
                    "target": tgt_nid,
                    "colour": "#94a3b8",
                    "edge_type": "foreign-key",
                    "fk_column": fk_col,
                    "label": fk_col,
                }
            })
            seen_edges.add(eid)

    total_groups = sum(1 for n in nodes if n["data"].get("type") == "group")
    total_elements = sum(len(r.elements) for r in results)

    return {
        "nodes": nodes,
        "edges": edges,
        "meta": {
            "tables": len(results),
            "groups": total_groups,
            "total_elements": total_elements,
            "fk_edges": len([e for e in edges if e["data"].get("edge_type") == "foreign-key"]),
        },
    }


def get_elements(
    results: list[IngestionResult],
    doc_id: str,
    group_value: str,
) -> list[dict]:
    """Return elements from a specific doc matching an optional group filter."""
    result = next((r for r in results if r.doc_id == doc_id), None)
    if not result:
        return []

    def _matches(el) -> bool:
        if not group_value:
            return True
        row = _parse_row(el.text)
        return any(v == group_value for v in row.values())

    return [
        {
            "element_id": el.element_id,
            "type": el.type,
            "text": el.text,
            "section_path": el.section_path,
            "normalized_values": el.metadata.get("normalized_values", {}),
            "row_id": el.metadata.get("row_id", ""),
        }
        for el in result.elements
        if _matches(el)
    ]


def get_stats(results: list[IngestionResult]) -> dict[str, Any]:
    """Return summary statistics for the sidebar."""
    stats: dict[str, Any] = {}
    for result in results:
        if not result.elements:
            continue
        tbl = result.elements[0].metadata.get("table", "unknown")
        group_col = _GROUP_COLUMN.get(tbl)
        group_counts: Counter = Counter()
        for el in result.elements:
            row = _parse_row(el.text)
            val = row.get(group_col, "(other)") if group_col else "(all)"
            group_counts[val] += 1

        # Numeric column aggregates
        numeric_totals: dict[str, float] = defaultdict(float)
        numeric_counts: dict[str, int] = defaultdict(int)
        for el in result.elements:
            for k, v in el.metadata.get("normalized_values", {}).items():
                try:
                    numeric_totals[k] += float(v)
                    numeric_counts[k] += 1
                except (ValueError, TypeError):
                    pass

        stats[tbl] = {
            "row_count": len(result.elements),
            "group_col": group_col,
            "groups": dict(group_counts),
            "numeric_averages": {
                k: round(numeric_totals[k] / numeric_counts[k], 2)
                for k in numeric_counts
            },
        }
    return stats
