"""Starlette ASGI app for the amorphous ingest viewer."""
from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ake.ingestion.amorphous_pipeline import AmorphousIngestionResult
from ake.ontology.graph import build_graph
from ake.ontology.model import Ontology

_STATIC = Path(__file__).parent / "static"

_TABLE_COLOURS = [
    "#0891b2", "#7c3aed", "#16a34a", "#e11d48",
    "#ea580c", "#d97706", "#0d9488", "#9333ea",
]


def _parse_row(text: str) -> dict[str, str]:
    row: dict[str, str] = {}
    for line in text.splitlines():
        if ": " in line:
            col, _, val = line.partition(": ")
            row[col.strip()] = val.strip()
    return row


def create_app(result: AmorphousIngestionResult, ontology: Ontology) -> Starlette:
    _graph = build_graph(ontology, result)

    # Pre-compute colour map once
    _colour_by_table = {
        tbl.name: _TABLE_COLOURS[i % len(_TABLE_COLOURS)]
        for i, tbl in enumerate(result.tables)
    }

    async def index(_: Request) -> HTMLResponse:
        return HTMLResponse((_STATIC / "index.html").read_text())

    async def api_graph(_: Request) -> JSONResponse:
        return JSONResponse(_graph)

    async def api_tables(_: Request) -> JSONResponse:
        tables = []
        for tbl in result.tables:
            cls = ontology.class_by_table.get(tbl.name)
            columns = []
            if cls:
                columns = [
                    {
                        "name": p.column,
                        "owl_name": p.name,
                        "type": p.datatype,
                        "semantic_role": p.semantic_role,
                        "nullable": p.nullable,
                    }
                    for p in cls.properties
                ]
            tables.append({
                "name": tbl.name,
                "label": tbl.name.replace("_", " ").title(),
                "colour": _colour_by_table[tbl.name],
                "row_count": tbl.row_count,
                "doc_id": tbl.result.doc_id,
                "class_name": cls.name if cls else tbl.name.title(),
                "columns": columns,
            })
        return JSONResponse({"tables": tables})

    async def api_relationships(_: Request) -> JSONResponse:
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
                "owl_property": owl_rel.name if owl_rel else None,
                "source_colour": _colour_by_table.get(r.source_table, "#475569"),
                "target_colour": _colour_by_table.get(r.target_table, "#475569"),
            })
        return JSONResponse({"relationships": rels})

    async def api_rows(request: Request) -> JSONResponse:
        table_name = request.query_params.get("table", "")
        page = max(0, int(request.query_params.get("page", 0)))
        limit = min(int(request.query_params.get("limit", 50)), 200)
        col_filter = request.query_params.get("column", "")
        val_filter = request.query_params.get("value", "").strip().lower()

        tbl = next((t for t in result.tables if t.name == table_name), None)
        if not tbl:
            return JSONResponse({"rows": [], "count": 0, "total": 0})

        all_rows: list[dict] = []
        for el in tbl.result.elements:
            fields = _parse_row(el.text)
            if val_filter:
                col_val = fields.get(col_filter, "").lower() if col_filter else ""
                any_val = any(val_filter in v.lower() for v in fields.values())
                if col_filter and col_val != val_filter:
                    continue
                if not col_filter and not any_val:
                    continue
            all_rows.append({
                "element_id": el.element_id,
                "row_id": el.metadata.get("row_id", ""),
                "fields": fields,
                "normalized_values": el.metadata.get("normalized_values", {}),
            })

        total = len(all_rows)
        return JSONResponse({
            "rows": all_rows[page * limit:(page + 1) * limit],
            "count": min(limit, max(0, total - page * limit)),
            "total": total,
            "page": page,
            "limit": limit,
        })

    async def api_ontology(_: Request) -> JSONResponse:
        return JSONResponse({
            "dataset": ontology.dataset_name,
            "namespace": ontology.namespace,
            "generated_at": ontology.generated_at,
            "classes": [
                {
                    "name": cls.name,
                    "label": cls.label,
                    "table": cls.table,
                    "row_count": cls.row_count,
                    "properties": [
                        {
                            "name": p.name,
                            "column": p.column,
                            "datatype": p.datatype,
                            "semantic_role": p.semantic_role,
                            "nullable": p.nullable,
                        }
                        for p in cls.properties
                    ],
                }
                for cls in ontology.classes
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
        })

    async def api_stats(_: Request) -> JSONResponse:
        stats: dict[str, Any] = {}
        for tbl in result.tables:
            col_names = [
                c["name"]
                for c in (
                    tbl.result.elements[0].metadata.get("column_schema", [])
                    if tbl.result.elements else []
                )
            ]
            freq: dict[str, Counter] = defaultdict(Counter)
            numeric: dict[str, list[float]] = defaultdict(list)

            for el in tbl.result.elements:
                fields = _parse_row(el.text)
                norms = el.metadata.get("normalized_values", {})
                for col in col_names:
                    val = fields.get(col, "")
                    if not val:
                        continue
                    try:
                        numeric[col].append(float(norms.get(col, val)))
                    except (ValueError, TypeError):
                        freq[col][val] += 1

            col_stats: dict[str, Any] = {}
            for col in col_names:
                if col in numeric and numeric[col]:
                    vals = numeric[col]
                    col_stats[col] = {
                        "type": "numeric",
                        "count": len(vals),
                        "min": round(min(vals), 2),
                        "max": round(max(vals), 2),
                        "avg": round(sum(vals) / len(vals), 2),
                    }
                elif col in freq:
                    col_stats[col] = {
                        "type": "categorical",
                        "distinct": len(freq[col]),
                        "top": [{"value": v, "count": c} for v, c in freq[col].most_common(8)],
                    }

            stats[tbl.name] = {"row_count": tbl.row_count, "columns": col_stats}

        return JSONResponse(stats)

    return Starlette(routes=[
        Route("/", index),
        Route("/api/graph", api_graph),
        Route("/api/tables", api_tables),
        Route("/api/relationships", api_relationships),
        Route("/api/rows", api_rows),
        Route("/api/ontology", api_ontology),
        Route("/api/stats", api_stats),
    ])
