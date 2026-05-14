
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import Any

from mcp.server.fastmcp import FastMCP
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.cors import CORSMiddleware

from ake.compiler.artifact import DomainArtifact
from ake.config import settings as _default_settings
from ake.ingestion.pipeline import IngestionPipeline
from ake.mcp.registry import (
    ArtifactTypeRegistration,
    get_registration,
    list_domains,
    list_registrations,
)
from ake.query.execute import execute as query_execute, get_trace as _get_trace
from ake.query.interface import Query, QueryBudget, QueryResult
from ake.store.artifact_store import ArtifactStore, _row_to_artifact, artifacts_table
from ake.store.element_store import ElementStore

"""MCP server — exposes AKE's capability surface via polymorphic resources and tools (F011).

Resources:
  ake://domains
  ake://domains/{domain_name}
  ake://schema/{artifact_type}
  ake://artifacts/{artifact_type}/{entity_id}
  ake://artifacts/{artifact_type}/{entity_id}/{fiscal_year}
  ake://elements/{doc_id}/{element_id}
  ake://citations/{artifact_id}

Tools:
  ake_query
  ake_list_artifact_types
  ake_describe_schema
  ake_get_artifact
  ake_ingest_document
  ake_ingest_status
  ake_list_entities
  ake_get_trace
"""


logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# Server setup
# ═════════════════════════════════════════════════════════════════════════════

mcp = FastMCP(
    name="Amorphous Knowledge Engine",
    instructions=(
        "AKE provides typed, cited knowledge artifacts compiled from documents, "
        "tabular data, and knowledge graphs. All responses carry a _source_type "
        "discriminator ('document', 'tabular', 'graph') and a citation trail. "
        "Use ake_list_artifact_types to discover available knowledge domains, "
        "then ake_query to retrieve cited answers."
    ),
)


def _get_stores() -> tuple[ArtifactStore, ElementStore]:
    """Get artifact and element stores backed by the shared session factory."""
    from ake.db.engine import AsyncSessionLocal

    return ArtifactStore(AsyncSessionLocal), ElementStore(AsyncSessionLocal)


def _get_session() -> AsyncSession:
    """Factory function used by query_execute to get a fresh session."""
    from ake.db.engine import AsyncSessionLocal

    async def factory():
        return AsyncSessionLocal()

    raise NotImplementedError("use _get_stores for direct session access")


# ═════════════════════════════════════════════════════════════════════════════
# Polymorphic envelope helper
# ═════════════════════════════════════════════════════════════════════════════


def _wrap_artifact(artifact: DomainArtifact, registration: ArtifactTypeRegistration | None = None) -> dict[str, Any]:
    """Wrap a DomainArtifact in the polymorphic envelope."""
    source_type = "document"  # default; ideally derived from artifact provenance
    # Ratio of fields that have at least one citation to total payload fields.
    # Named "ratio" not "coverage" because it counts fields, not character spans.
    fields_cited_ratio = (
        len(artifact.field_citations) / max(len(artifact.payload), 1)
        if artifact.payload else 0.0
    )

    return {
        "_ake_version": "1.0",
        "_artifact_type": artifact.artifact_type,
        "_source_type": source_type,
        "_entity_id": artifact.entity_id,
        "_compiled_at": artifact.compiled_at.isoformat(),
        "_fields_cited_ratio": round(fields_cited_ratio, 2),
        "data": artifact.payload,
        "citations": [
            {
                "field": field_name,
                **citation.model_dump(),
            }
            for field_name, citation in artifact.field_citations.items()
        ],
        "artifact_id": artifact.artifact_id,
        "doc_id": artifact.doc_id,
        "fiscal_year": artifact.fiscal_year,
    }


def _wrap_envelope(data: dict[str, Any], source_type: str = "document", artifact_type: str = "unknown") -> dict[str, Any]:
    """Wrap an arbitrary data dict in the polymorphic envelope."""
    return {
        "_ake_version": "1.0",
        "_artifact_type": artifact_type,
        "_source_type": source_type,
        "_entity_id": data.get("entity_id", ""),
        "_compiled_at": datetime.now(timezone.utc).isoformat(),
        "_citation_coverage": 1.0,
        "data": data,
        "citations": [],
    }


# ═════════════════════════════════════════════════════════════════════════════
# Resources
# ═════════════════════════════════════════════════════════════════════════════


@mcp.resource("ake://domains")
def resource_list_domains() -> str:
    """List all registered domains."""
    domains = list_domains()
    result: list[dict[str, Any]] = []
    for d in domains:
        result.append({
            "name": d.name,
            "description": d.description,
            "artifact_types": d.artifact_types,
            "eval_status": d.eval_status,
        })
    return json.dumps(result, indent=2)


@mcp.resource("ake://domains/{domain_name}")
def resource_get_domain(domain_name: str) -> str:
    """Get details for a specific domain."""
    result: dict[str, Any] | None = None
    for d in list_domains():
        if d.name == domain_name:
            regs = list_registrations(domain=domain_name)
            result = {
                "name": d.name,
                "description": d.description,
                "artifact_types": d.artifact_types,
                "eval_status": d.eval_status,
                "schemas": {
                    r.artifact_type: r.json_schema for r in regs
                },
            }
            break

    if result is None:
        return json.dumps({"error": f"domain '{domain_name}' not found"})

    return json.dumps(result, indent=2)


@mcp.resource("ake://schema/{artifact_type}")
def resource_get_schema(artifact_type: str) -> str:
    """Return JSON Schema for a specific artifact type."""
    reg = get_registration(artifact_type)
    if reg is None:
        return json.dumps({"error": f"artifact type '{artifact_type}' not registered"})

    result = {
        "artifact_type": reg.artifact_type,
        "domain": reg.domain,
        "description": reg.description,
        "json_schema": reg.json_schema,
        "nullable_fields": reg.nullable_fields,
        "promoted_filters": reg.promoted_filters,
        "source_types": reg.source_types,
        "example": reg.example,
    }
    return json.dumps(result, indent=2)


@mcp.resource("ake://artifacts/{artifact_type}/{entity_id}")
async def resource_get_artifact(artifact_type: str, entity_id: str) -> str:
    """Most recent compiled artifact for an entity."""
    artifact_store, _ = _get_stores()
    artifacts = await artifact_store.get_by_entity(entity_id, artifact_type)
    if not artifacts:
        return json.dumps({"error": f"no artifact found for {artifact_type}/{entity_id}"})

    reg = get_registration(artifact_type)
    return json.dumps(_wrap_artifact(artifacts[0], reg), indent=2)


@mcp.resource("ake://artifacts/{artifact_type}/{entity_id}/{fiscal_year}")
async def resource_get_artifact_by_year(artifact_type: str, entity_id: str, fiscal_year: str) -> str:
    """Version-specific artifact by fiscal year."""
    artifact_store, _ = _get_stores()
    try:
        fy = int(fiscal_year)
    except ValueError:
        return json.dumps({"error": f"invalid fiscal_year: {fiscal_year}"})

    artifacts = await artifact_store.get_by_entity(entity_id, artifact_type, fy)
    if not artifacts:
        return json.dumps({"error": f"no artifact for {artifact_type}/{entity_id}/{fiscal_year}"})

    reg = get_registration(artifact_type)
    return json.dumps(_wrap_artifact(artifacts[0], reg), indent=2)


@mcp.resource("ake://elements/{doc_id}/{element_id}")
async def resource_get_element(doc_id: str, element_id: str) -> str:
    """Raw source element."""
    _, element_store = _get_stores()
    elements = await element_store.get_by_doc_id(doc_id)
    for el in elements:
        if el.element_id == element_id:
            return json.dumps({
                "doc_id": el.doc_id,
                "element_id": el.element_id,
                "type": el.type,
                "text": el.text,
                "page": el.page,
                "section_path": el.section_path,
            }, indent=2)
    return json.dumps({"error": f"element {doc_id}/{element_id} not found"})


@mcp.resource("ake://citations/{artifact_id}")
async def resource_get_citations(artifact_id: str) -> str:
    """All citations for an artifact."""
    artifact_store, _ = _get_stores()
    artifact = await artifact_store.get_by_id(artifact_id)
    if artifact is None:
        return json.dumps({"error": f"artifact {artifact_id} not found"})

    citations = [
        {"field": field_name, **citation.model_dump()}
        for field_name, citation in artifact.field_citations.items()
    ]
    return json.dumps({
        "artifact_id": artifact.artifact_id,
        "entity_id": artifact.entity_id,
        "artifact_type": artifact.artifact_type,
        "citations": citations,
    }, indent=2)


# ═════════════════════════════════════════════════════════════════════════════
# Tools — Query
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def ake_query(
    ask: str,
    shape: dict[str, Any],
    filters: dict[str, Any] | None = None,
    contexts: list[str] | None = None,
    ground: bool = True,
    budget_max_artifacts: int = 20,
    budget_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    """Execute a declarative query against the artifact store.

    Maps directly to the declarative query interface. The composer reshapes
    artifacts into query.shape. All responses carry citations when ground=True.

    Args:
        ask: Natural-language question.
        shape: The desired output structure as a flat or nested dict where each
            key is a field name and each value is either None (scalar field) or
            a nested dict (for nested objects). Use plain field names, NOT JSON
            Schema property objects. For a list of procedure names with SLAs:
            ``{"results": [{"procedure_name": None, "sla": None}]}``.
            Passing ``{"field": {"type": "string"}}`` will echo schema back.
        filters: Promoted-column filters — keys must be entity_id, artifact_type,
            or fiscal_year. For entity lookups, prefer ake_get_artifact.
        contexts: Artifact types to search, e.g. ["kb_procedure"]. When provided
            the planner fetches all artifacts of those types and the composer
            answers the question. Leave empty to infer from ask.
        ground: Require citations in response.
        budget_max_artifacts: Maximum artifacts to retrieve.
        budget_timeout_seconds: Query timeout in seconds.
    """
    query = Query(
        ask=ask,
        shape=shape,
        filters=filters or {},
        contexts=contexts or [],
        ground=ground,
        budget=QueryBudget(
            max_artifacts=budget_max_artifacts,
            timeout_seconds=budget_timeout_seconds,
        ),
    )

    from ake.db.engine import AsyncSessionLocal

    async with AsyncSessionLocal() as session:
        result: QueryResult = await query_execute(query, principal="mcp", session=session)

    return {
        "_ake_version": "1.0",
        "query_id": result.query_id,
        "data": result.data,
        "citations": [
            {
                "field": c.field,
                "element_id": c.element_id,
                "verbatim_span": c.verbatim_span,
                "doc_id": c.doc_id,
                "confidence": c.confidence,
            }
            for c in result.citations
        ],
        "artifacts_used": result.artifacts_used,
        "latency_ms": result.latency_ms,
        "token_cost": result.token_cost,
    }


@mcp.tool()
def ake_list_artifact_types(domain: str | None = None) -> dict[str, Any]:
    """List all registered artifact types, optionally filtered by domain.

    The primary discovery tool for agents building queries.
    """
    regs = list_registrations(domain)
    types_list = []
    for r in regs:
        types_list.append({
            "artifact_type": r.artifact_type,
            "domain": r.domain,
            "description": r.description,
            "fields": list(r.json_schema.get("properties", {}).keys()),
            "source_types": r.source_types,
        })

    return {"artifact_types": types_list, "total": len(types_list)}


@mcp.tool()
def ake_describe_schema(artifact_type: str) -> dict[str, Any]:
    """Return the full schema for an artifact type plus an annotated example.

    Agents use this to construct query.shape conformant JSON.
    """
    reg = get_registration(artifact_type)
    if reg is None:
        return {"error": f"artifact type '{artifact_type}' not registered"}

    return {
        "artifact_type": reg.artifact_type,
        "domain": reg.domain,
        "description": reg.description,
        "json_schema": reg.json_schema,
        "nullable_fields": reg.nullable_fields,
        "promoted_filters": reg.promoted_filters,
        "source_types": reg.source_types,
        "example": reg.example,
    }


@mcp.tool()
async def ake_get_artifact(
    artifact_type: str,
    entity_id: str,
    fiscal_year: int | None = None,
) -> dict[str, Any]:
    """Direct artifact retrieval by identity, bypassing the planner.

    For agents that already know the exact artifact they need.
    """
    artifact_store, _ = _get_stores()
    artifacts = await artifact_store.get_by_entity(entity_id, artifact_type, fiscal_year)
    if not artifacts:
        return {"error": f"no artifact found for {artifact_type}/{entity_id}"}

    reg = get_registration(artifact_type)
    return _wrap_artifact(artifacts[0], reg)


# ═════════════════════════════════════════════════════════════════════════════
# Tools — Ingestion
# ═════════════════════════════════════════════════════════════════════════════

# In-memory job tracker (production would use a database table)
_ingestion_jobs: dict[str, dict[str, Any]] = {}


@mcp.tool()
async def ake_ingest_document(
    source_url: str,
    source_type: str = "document",
    acl_principals: list[str] | None = None,
    domain: str | None = None,
) -> dict[str, Any]:
    """Trigger ingestion and compilation for a single source.

    source_type is 'document', 'tabular', or 'graph'.
    Returns a job ID for polling; compilation is asynchronous.
    """
    import uuid

    job_id = str(uuid.uuid4())
    _ingestion_jobs[job_id] = {
        "job_id": job_id,
        "status": "pending",
        "source_url": source_url,
        "source_type": source_type,
        "domain": domain,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    # Fire-and-forget: start ingestion in background
    asyncio.create_task(_run_ingestion(job_id, source_url, source_type, acl_principals or []))

    return {"job_id": job_id, "status": "pending"}


async def _run_ingestion(
    job_id: str,
    source_url: str,
    source_type: str,
    acl_principals: list[str],
) -> None:
    """Background ingestion task."""
    try:
        _ingestion_jobs[job_id]["status"] = "parsing"

        pipeline = IngestionPipeline()
        metadata = {
            "source_url": source_url,
            "acl_principals": acl_principals,
        }

        if source_url.startswith("http://") or source_url.startswith("https://"):
            import tempfile
            import urllib.request

            with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as tmp:
                data = urllib.request.urlopen(source_url).read()
                tmp.write(data)
                tmp_path = tmp.name

            result = await pipeline.ingest_file(tmp_path, metadata)
            import os

            os.unlink(tmp_path)
        else:
            result = await pipeline.ingest_file(source_url, metadata)

        _ingestion_jobs[job_id]["status"] = "compiling"
        _ingestion_jobs[job_id]["doc_id"] = result.doc_id
        _ingestion_jobs[job_id]["element_count"] = len(result.elements)
        _ingestion_jobs[job_id]["status"] = "complete"
    except Exception as exc:
        logger.exception("ingestion_failed job_id=%s", job_id)
        _ingestion_jobs[job_id]["status"] = "failed"
        _ingestion_jobs[job_id]["errors"] = str(exc)


@mcp.tool()
def ake_ingest_status(job_id: str) -> dict[str, Any]:
    """Poll an ingestion job status.

    Returns status: 'pending', 'parsing', 'compiling', 'complete', or 'failed'.
    """
    job = _ingestion_jobs.get(job_id)
    if job is None:
        return {"error": f"job '{job_id}' not found"}

    return {
        "job_id": job["job_id"],
        "status": job["status"],
        "doc_id": job.get("doc_id"),
        "artifact_count": job.get("artifact_count"),
        "citation_coverage": job.get("citation_coverage"),
        "errors": job.get("errors"),
    }


# ═════════════════════════════════════════════════════════════════════════════
# Tools — Discovery & Introspection
# ═════════════════════════════════════════════════════════════════════════════


@mcp.tool()
async def ake_list_entities(
    artifact_type: str,
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List all entities with compiled artifacts of a given type.

    Useful for agents building enumeration queries.
    """
    from ake.db.engine import AsyncSessionLocal

    from sqlalchemy import select, func, distinct
    from ake.store.artifact_store import artifacts_table

    async with AsyncSessionLocal() as session:
        await session.execute(text("SET app.current_principals = 'mcp'"))

        stmt = (
            select(
                artifacts_table.c.entity_id,
                func.count(artifacts_table.c.artifact_id).label("artifact_count"),
                func.max(artifacts_table.c.compiled_at).label("latest_compiled_at"),
            )
            .where(artifacts_table.c.artifact_type == artifact_type)
            .group_by(artifacts_table.c.entity_id)
            .order_by(artifacts_table.c.entity_id)
        )

        result = await session.execute(stmt)
        rows = result.fetchall()

    entities = []
    for row in rows:
        entities.append({
            "entity_id": row.entity_id,
            "artifact_count": row.artifact_count,
            "latest_compiled_at": row.latest_compiled_at.isoformat() if row.latest_compiled_at else None,
        })

    return {"artifact_type": artifact_type, "entities": entities, "total": len(entities)}


@mcp.tool()
def ake_get_trace(query_id: str) -> dict[str, Any]:
    """Return the execution trace for a past query.

    Pass the query_id returned by ake_query. Traces are held in memory for
    the lifetime of the server process (up to 500 entries, LRU eviction).
    Returns status, artifact_types planned, artifacts retrieved, elapsed time,
    and token cost — useful for diagnosing empty results or slow queries.
    """
    trace = _get_trace(query_id)
    if trace is None:
        return {
            "error": "trace not found — query_id may be from a previous server "
            "process or older than the eviction window",
            "query_id": query_id,
        }
    return trace


# ═════════════════════════════════════════════════════════════════════════════
# Run entry point
# ═════════════════════════════════════════════════════════════════════════════


def run_stdio() -> None:
    """Run the MCP server over stdio (for Claude Desktop / local agents)."""
    mcp.run()


def run_sse(
    host: str = "0.0.0.0",
    port: int = 8000,
    ssl_certfile: str | None = None,
    ssl_keyfile: str | None = None,
    ssl_keyfile_password: str | None = None,
) -> None:
    """Run the MCP server over SSE (for remote agents).

    To serve over HTTPS, provide ssl_certfile and ssl_keyfile paths.
    """
    import uvicorn

    app = mcp.sse_app()
    app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],  # In production, should set specific domains
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )
    
    uvicorn.run(
        app,
        host=host,
        port=port,
        ssl_certfile=ssl_certfile,
        ssl_keyfile=ssl_keyfile,
        ssl_keyfile_password=ssl_keyfile_password,
    )
