"""Starlette ASGI application for the knowledge graph viewer."""
from __future__ import annotations

import json
from pathlib import Path

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from ake.ingestion.pipeline import IngestionResult

from .graph import build_graph, get_elements

_STATIC = Path(__file__).parent / "static"


def create_app(results: list[IngestionResult]) -> Starlette:
    """Return the ASGI app pre-loaded with the given ingestion results."""

    # Build the graph once at startup — it's immutable for this session.
    _graph = build_graph(results)

    async def index(_: Request) -> HTMLResponse:
        return HTMLResponse((_STATIC / "index.html").read_text())

    async def api_graph(_: Request) -> JSONResponse:
        return JSONResponse(_graph)

    async def api_elements(request: Request) -> JSONResponse:
        doc_id = request.query_params.get("doc_id", "")
        raw = request.query_params.get("section_path", "[]")
        try:
            section_path: list[str] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            section_path = []
        elements = get_elements(results, doc_id, section_path)
        return JSONResponse({"elements": elements, "count": len(elements)})

    async def api_docs(_: Request) -> JSONResponse:
        from pathlib import Path as P
        docs = [
            {
                "doc_id": r.doc_id,
                "label": P(r.source_url.split("?")[0]).stem.replace("-", " ").replace("_", " ").title(),
                "source_url": r.source_url,
                "element_count": len(r.elements),
            }
            for r in results
        ]
        return JSONResponse({"documents": docs})

    return Starlette(
        routes=[
            Route("/", index),
            Route("/api/graph", api_graph),
            Route("/api/elements", api_elements),
            Route("/api/documents", api_docs),
        ]
    )
