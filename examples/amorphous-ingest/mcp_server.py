#!/usr/bin/env python3
"""
AKE Amorphous Ingest — standalone MCP server (no database required).

Scans a data directory with the amorphous pipeline, derives the ontology,
and serves the result via MCP so any agent can discover, query, and inspect
the dataset without a Postgres connection.

Resources
---------
  ake://amorphous/{dataset}/tables           — list all tables with row counts
  ake://amorphous/{dataset}/schema/{table}   — column schema + semantic roles
  ake://amorphous/{dataset}/relationships    — all inferred FK relationships
  ake://amorphous/{dataset}/ontology         — OWL class model summary

Tools
-----
  list_tables()                              — tables with row counts + columns
  get_schema(table_name)                     — columns with types + roles
  query_rows(table_name, column?, value?, limit?)   — filter rows
  get_relationships()                        — all inferred FK relationships
  describe_ontology()                        — full OWL class/property model
  describe_class(table_name)                 — one class with object properties

Usage
-----
  uv run python examples/amorphous-ingest/mcp_server.py
  uv run python examples/amorphous-ingest/mcp_server.py --data data/ --dataset-name acme
  uv run python examples/amorphous-ingest/mcp_server.py --stdio
  uv run python examples/amorphous-ingest/mcp_server.py --host 0.0.0.0 --port 8001
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

try:
    import pyarrow  # noqa: F401
except ImportError:
    print(
        "This example requires the 'ingestion' dependency group.\n"
        "Run:  uv sync --group ingestion"
    )
    sys.exit(1)

_HERE = Path(__file__).parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))

from mcp.server.fastmcp import FastMCP

from ake.ingestion.amorphous_pipeline import AmorphousIngestionResult
from ake.ontology.builder import build_ontology
from ake.ontology.model import Ontology

# ── Module-level state (populated at startup before server begins) ─────────
_result: AmorphousIngestionResult | None = None
_ontology: Ontology | None = None

mcp = FastMCP(
    name="AKE Amorphous Dataset",
    instructions=(
        "Use list_tables() first to discover available tables, then get_schema() "
        "to understand column types and semantic roles, then query_rows() to fetch "
        "filtered data. Use get_relationships() to understand how tables connect, "
        "and describe_ontology() for the full OWL class model."
    ),
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _parse_row(text: str) -> dict[str, str]:
    row: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            col, _, val = line.partition(": ")
            row[col.strip()] = val.strip()
    return row


def _require_loaded() -> tuple[AmorphousIngestionResult, Ontology]:
    if _result is None or _ontology is None:
        raise RuntimeError("Dataset not yet loaded — server is still initialising")
    return _result, _ontology


# ── MCP Resources ──────────────────────────────────────────────────────────────

@mcp.resource("ake://amorphous/{dataset}/tables")
def resource_tables(dataset: str) -> str:
    result, _ = _require_loaded()
    tables = [
        {"name": t.name, "row_count": t.row_count,
         "columns": [c.name for c in t.columns]}
        for t in result.tables
    ]
    return json.dumps({"dataset": result.dataset_name, "tables": tables}, indent=2)


@mcp.resource("ake://amorphous/{dataset}/schema/{table}")
def resource_schema(dataset: str, table: str) -> str:
    _, ontology = _require_loaded()
    cls = ontology.class_by_table.get(table)
    if not cls:
        return json.dumps({"error": f"Table '{table}' not found"})
    return json.dumps({
        "table": table,
        "class": cls.name,
        "namespace": ontology.namespace,
        "columns": [
            {"name": p.column, "owl_name": p.name,
             "type": p.datatype, "semantic_role": p.semantic_role,
             "nullable": p.nullable}
            for p in cls.properties
        ],
    }, indent=2)


@mcp.resource("ake://amorphous/{dataset}/relationships")
def resource_relationships(dataset: str) -> str:
    result, _ = _require_loaded()
    return json.dumps({
        "dataset": result.dataset_name,
        "relationships": [
            {"source_table": r.source_table, "source_column": r.source_column,
             "target_table": r.target_table, "target_column": r.target_column,
             "confidence": r.confidence, "evidence": r.evidence}
            for r in result.relationships
        ],
    }, indent=2)


@mcp.resource("ake://amorphous/{dataset}/ontology")
def resource_ontology(dataset: str) -> str:
    _, ontology = _require_loaded()
    return json.dumps({
        "dataset": ontology.dataset_name,
        "namespace": ontology.namespace,
        "generated_at": ontology.generated_at,
        "classes": [
            {"name": c.name, "table": c.table, "row_count": c.row_count,
             "properties": [
                 {"name": p.name, "column": p.column, "type": p.datatype,
                  "semantic_role": p.semantic_role}
                 for p in c.properties
             ]}
            for c in ontology.classes
        ],
        "object_properties": [
            {"name": r.name, "domain": r.domain, "range": r.range,
             "source_column": r.source_column, "confidence": r.confidence}
            for r in ontology.relationships
        ],
    }, indent=2)


# ── MCP Tools ──────────────────────────────────────────────────────────────────

@mcp.tool()
def list_tables() -> dict[str, Any]:
    """List all tables in the dataset with their row counts and column names."""
    result, ontology = _require_loaded()
    tables = []
    for tbl in result.tables:
        cls = ontology.class_by_table.get(tbl.name)
        tables.append({
            "name": tbl.name,
            "owl_class": cls.name if cls else tbl.name,
            "row_count": tbl.row_count,
            "columns": [c.name for c in tbl.columns],
        })
    return {
        "dataset": result.dataset_name,
        "table_count": len(tables),
        "tables": tables,
    }


@mcp.tool()
def get_schema(table_name: str) -> dict[str, Any]:
    """Return the column schema for a table including semantic roles and XSD types.

    Args:
        table_name: Name of the table (e.g. "employees").
    """
    _, ontology = _require_loaded()
    cls = ontology.class_by_table.get(table_name)
    if not cls:
        return {"error": f"Table '{table_name}' not found. Use list_tables() to see available tables."}

    rels_out = [
        r for r in ontology.relationships
        if r.source_table == table_name or r.target_table == table_name
    ]

    return {
        "table": table_name,
        "owl_class": cls.name,
        "row_count": cls.row_count,
        "columns": [
            {
                "name": p.column,
                "owl_property": p.name,
                "xsd_type": p.datatype,
                "semantic_role": p.semantic_role,
                "nullable": p.nullable,
            }
            for p in cls.properties
        ],
        "relationships": [
            {
                "direction": "outbound" if r.source_table == table_name else "inbound",
                "source_table": r.source_table,
                "source_column": r.source_column,
                "target_table": r.target_table,
                "target_column": r.target_column,
                "confidence": r.confidence,
                "evidence": r.evidence,
            }
            for r in rels_out
        ],
    }


@mcp.tool()
def query_rows(
    table_name: str,
    column: str = "",
    value: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch rows from a table, optionally filtering by column value.

    Args:
        table_name: Name of the table to query (e.g. "employees").
        column:     Column name to filter on (e.g. "department"). Empty = search all columns.
        value:      Value to match (case-insensitive substring for strings). Empty = no filter.
        limit:      Maximum number of rows to return (default 20, max 100).
    """
    result, _ = _require_loaded()
    tbl = next((t for t in result.tables if t.name == table_name), None)
    if not tbl:
        return {"error": f"Table '{table_name}' not found. Use list_tables() to see available tables."}

    limit = min(max(1, limit), 100)
    val_lower = value.strip().lower()

    rows: list[dict] = []
    for el in tbl.result.elements:
        fields = _parse_row(el.text)
        if val_lower:
            if column:
                if fields.get(column, "").lower() != val_lower:
                    continue
            else:
                if not any(val_lower in v.lower() for v in fields.values()):
                    continue
        rows.append({
            "row_id": el.metadata.get("row_id", ""),
            "fields": fields,
            "normalized_values": el.metadata.get("normalized_values", {}),
        })
        if len(rows) >= limit:
            break

    return {
        "table": table_name,
        "filter": {"column": column, "value": value} if val_lower else None,
        "returned": len(rows),
        "rows": rows,
    }


@mcp.tool()
def get_relationships() -> dict[str, Any]:
    """Return all inferred FK relationships between tables with confidence scores.

    Relationships with evidence='both' are confirmed by both naming convention
    and value overlap. Evidence='naming' means structural match only.
    """
    result, ontology = _require_loaded()
    rels = []
    for r in result.relationships:
        owl_rel = next(
            (x for x in ontology.relationships
             if x.source_table == r.source_table and x.source_column == r.source_column),
            None,
        )
        rels.append({
            "source_table": r.source_table,
            "source_column": r.source_column,
            "target_table": r.target_table,
            "target_column": r.target_column,
            "confidence": r.confidence,
            "evidence": r.evidence,
            "owl_object_property": owl_rel.name if owl_rel else None,
        })

    return {"dataset": result.dataset_name, "relationship_count": len(rels), "relationships": rels}


@mcp.tool()
def describe_ontology() -> dict[str, Any]:
    """Return the full OWL 2 class model: classes, data properties, and object properties."""
    _, ontology = _require_loaded()
    return {
        "dataset": ontology.dataset_name,
        "namespace": ontology.namespace,
        "generated_at": ontology.generated_at,
        "classes": [
            {
                "name": c.name,
                "table": c.table,
                "row_count": c.row_count,
                "data_properties": [
                    {"name": p.name, "column": p.column, "type": p.datatype,
                     "semantic_role": p.semantic_role}
                    for p in c.properties
                ],
            }
            for c in ontology.classes
        ],
        "object_properties": [
            {
                "name": r.name,
                "label": r.label,
                "domain": r.domain,
                "range": r.range,
                "source_column": r.source_column,
                "confidence": r.confidence,
                "evidence": r.evidence,
            }
            for r in ontology.relationships
        ],
    }


@mcp.tool()
def describe_class(table_name: str) -> dict[str, Any]:
    """Return the OWL class definition for one table, including all object properties.

    Args:
        table_name: Table name (e.g. "employees").
    """
    _, ontology = _require_loaded()
    cls = ontology.class_by_table.get(table_name)
    if not cls:
        return {"error": f"Table '{table_name}' not found."}

    inbound = [r for r in ontology.relationships if r.range == cls.name]
    outbound = [r for r in ontology.relationships if r.domain == cls.name]

    return {
        "class": cls.name,
        "table": cls.table,
        "namespace": ontology.namespace,
        "full_uri": f"{ontology.namespace}{cls.name}",
        "row_count": cls.row_count,
        "data_properties": [
            {"owl_name": p.name, "column": p.column, "range": p.datatype,
             "semantic_role": p.semantic_role, "nullable": p.nullable}
            for p in cls.properties
        ],
        "outbound_object_properties": [
            {"name": r.name, "range": r.range, "source_column": r.source_column,
             "confidence": r.confidence}
            for r in outbound
        ],
        "inbound_object_properties": [
            {"name": r.name, "domain": r.domain, "source_column": r.source_column,
             "confidence": r.confidence}
            for r in inbound
        ],
    }


# ── Startup ────────────────────────────────────────────────────────────────────

async def _load_dataset(data_dir: Path, dataset_name: str | None) -> None:
    global _result, _ontology
    from ake.ingestion.amorphous_pipeline import ingest_directory
    print(f"  Scanning {data_dir} …")
    _result = await ingest_directory(data_dir, dataset_name=dataset_name)
    print(f"  ✓ {len(_result.tables)} tables, {len(_result.all_elements)} elements, "
          f"{len(_result.relationships)} relationships")
    print("  Building ontology …")
    _ontology = build_ontology(_result)
    print(f"  ✓ {len(_ontology.classes)} classes, {len(_ontology.relationships)} object properties")


def main() -> None:
    default_data = _HERE / "data"
    parser = argparse.ArgumentParser(description="AKE Amorphous Ingest — MCP server")
    parser.add_argument("--data", default=str(default_data),
                        help=f"Data directory (default: {default_data})")
    parser.add_argument("--dataset-name", default=None)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--stdio", action="store_true",
                        help="Use stdio transport instead of SSE")
    args = parser.parse_args()

#    print("╔══════════════════════════════════════════════════════╗")
#    print("║  AKE — Amorphous Ingest MCP Server                   ║")
#    print("╚══════════════════════════════════════════════════════╝")
#    print()
#    print("  Loading dataset …")
    asyncio.run(_load_dataset(Path(args.data), args.dataset_name))

#    print()
#    print(f"  Dataset   : {_result.dataset_name}")
#    print(f"  Namespace : {_ontology.namespace}")
#    print()
#    print("  Resources:")
#    print(f"    ake://amorphous/{_result.dataset_name}/tables")
#    print(f"    ake://amorphous/{_result.dataset_name}/schema/{{table}}")
#    print(f"    ake://amorphous/{_result.dataset_name}/relationships")
#    print(f"    ake://amorphous/{_result.dataset_name}/ontology")
#    print()
#    print("  Tools:")
#    for tool in ("list_tables", "get_schema", "query_rows",
#                 "get_relationships", "describe_ontology", "describe_class"):
#        print(f"    • {tool}")
#    print()

    if args.stdio:
 #       print("  Transport: stdio")
        mcp.run(transport="stdio")
    else:
        print(f"  Transport: SSE  →  http://{args.host}:{args.port}/sse")
        print("  Press Ctrl+C to stop.\n")
        mcp.run(transport="sse", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
