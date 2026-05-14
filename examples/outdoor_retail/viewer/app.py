"""Starlette ASGI application for the outdoor retail data viewer."""
from __future__ import annotations

import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ake.ingestion.pipeline import IngestionResult

from .graph import build_graph, get_elements, get_stats

_STATIC = Path(__file__).parent / "static"


def create_app(results: list[IngestionResult]) -> Starlette:
    _graph = build_graph(results)
    _stats = get_stats(results)

    async def index(_: Request) -> HTMLResponse:
        return HTMLResponse((_STATIC / "index.html").read_text())

    async def api_graph(_: Request) -> JSONResponse:
        return JSONResponse(_graph)

    async def api_elements(request: Request) -> JSONResponse:
        doc_id = request.query_params.get("doc_id", "")
        group_value = request.query_params.get("group_value", "")
        elements = get_elements(results, doc_id, group_value)
        return JSONResponse({"elements": elements, "count": len(elements)})

    async def api_tables(_: Request) -> JSONResponse:
        tables = []
        for result in results:
            if not result.elements:
                continue
            tbl = result.elements[0].metadata.get("table", "unknown")
            tables.append({
                "doc_id": result.doc_id,
                "table": tbl,
                "label": tbl.replace("_", " ").title(),
                "source_url": result.source_url,
                "row_count": len(result.elements),
                "colour": {
                    "locations": "#0891b2",
                    "employees": "#7c3aed",
                    "products":  "#16a34a",
                    "inventory": "#e11d48",
                    "sales":     "#ea580c",
                }.get(tbl, "#475569"),
            })
        return JSONResponse({"tables": tables})

    async def api_stats(_: Request) -> JSONResponse:
        return JSONResponse(_stats)

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/graph", api_graph),
            Route("/api/elements", api_elements),
            Route("/api/tables", api_tables),
            Route("/api/stats", api_stats),
        ]
    )
