#!/usr/bin/env python3
"""
Acme Corp Knowledge Base — MCP server.

Completes the full AKE pipeline on the knowledgebase documents:
  1. Ingestion (F001)          — HTML → Element records
  2. Artifact compilation (F002) — Elements → typed, cited DomainArtifacts
  3. MCP registry (F011)       — register artifact types for agent discovery
  4. MCP server                — serve artifacts via SSE for agent consumption

After running this script, agents can discover and query the knowledge base
through the standard AKE MCP interface (ake_list_artifact_types, ake_query, etc.).

Usage
-----
  # Prerequisites
  export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
  export LLM_API_KEY=...            # or configure .env.local
  alembic upgrade head

  # Run the MCP server
  uv run python examples/knowledgebase/mcp_server.py
  uv run python examples/knowledgebase/mcp_server.py --port 8080 --host 0.0.0.0

  # Or skip compilation and only serve already-stored artifacts
  uv run python examples/knowledgebase/mcp_server.py --no-compile
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

# ── Dependency check ──────────────────────────────────────────────────────────
try:
    import unstructured  # noqa: F401
except ImportError:
    print(
        "This example requires the 'ingestion' dependency group.\n"
        "Run:  uv sync --group ingestion\n"
        "Then: uv run python examples/knowledgebase/mcp_server.py"
    )
    sys.exit(1)

from ake.compiler.artifact import DomainSchema, FieldSpec
from ake.compiler.artifact_compiler import ArtifactCompiler
from ake.ingestion.pipeline import IngestionPipeline, IngestionResult
from ake.llm.router import LLMRouter
from ake.llm.tools import ToolRegistry
from ake.mcp.registry import register
from ake.mcp.server import run_sse, run_stdio

DOCS_DIR = Path(__file__).parent / "docs"

# ── Document catalogue (same as ingest.py) ────────────────────────────────────
DOCUMENTS: list[dict[str, Any]] = [
    {
        "path": DOCS_DIR / "engineering-handbook.html",
        "metadata": {
            "source_url": "https://wiki.acme.com/engineering/handbook",
            "acl_principals": ["group:engineering", "group:product"],
            "doc_type": "handbook",
            "department": "engineering",
        },
    },
    {
        "path": DOCS_DIR / "hr-handbook.html",
        "metadata": {
            "source_url": "https://wiki.acme.com/hr/handbook",
            "acl_principals": ["group:all-employees"],
            "doc_type": "handbook",
            "department": "hr",
        },
    },
    {
        "path": DOCS_DIR / "security-policy.html",
        "metadata": {
            "source_url": "https://wiki.acme.com/security/policy",
            "acl_principals": ["group:all-employees"],
            "doc_type": "policy",
            "department": "security",
        },
    },
]


# ── Domain schemas ────────────────────────────────────────────────────────────
# Each schema describes one artifact type the knowledgebase can answer questions
# about.  The compiler uses these schemas to guide LLM extraction.


def _build_schemas() -> list[DomainSchema]:
    """Define the artifact types extractable from the knowledgebase documents.

    Each schema maps to content actually present in the source documents:
      - policies (security-policy.html)
      - procedures (engineering / HR handbooks)
      - general kb entries (catch-all for handbook sections)
    """
    return [
        DomainSchema(
            artifact_type="kb_policy",
            description=(
                "A company policy governing data, security, access, or incident "
                "response. Includes the policy name, classification level, "
                "owner department, and a one-paragraph summary."
            ),
            entity_id_field="policy_name",
            fields={
                "policy_name": FieldSpec(
                    description="Short, canonical name of the policy", type="str", required=True
                ),
                "classification": FieldSpec(
                    description="Data classification level this policy governs", type="str"
                ),
                "owner": FieldSpec(
                    description="Department or role that owns this policy", type="str"
                ),
                "summary": FieldSpec(
                    description="One-paragraph summary of what this policy requires", type="str"
                ),
            },
        ),
        DomainSchema(
            artifact_type="kb_procedure",
            description=(
                "A procedure, standard, or defined process from an engineering or "
                "HR handbook. Includes the procedure name, owning department, any "
                "SLA timeframe, and a one-paragraph description."
            ),
            entity_id_field="procedure_name",
            fields={
                "procedure_name": FieldSpec(
                    description="Short, canonical name of the procedure", type="str", required=True
                ),
                "department": FieldSpec(
                    description="Department this procedure belongs to", type="str"
                ),
                "sla": FieldSpec(
                    description="Response or completion SLA timeframe if specified", type="str"
                ),
                "summary": FieldSpec(
                    description="One-paragraph description of the procedure", type="str"
                ),
            },
        ),
    ]


# ── Ingestion (reused from ingest.py) ─────────────────────────────────────────


async def purge_knowledgebase_artifacts(schemas: list[DomainSchema]) -> int:
    """Delete all stored artifacts for the knowledgebase artifact types.

    Called before re-ingestion so stale rows (old entity_ids, pre-prompt-fix
    duplicates) don't survive alongside freshly compiled ones.

    Returns the number of rows deleted.
    """
    import sqlalchemy as sa

    from ake.db.engine import AsyncSessionLocal
    from ake.store.artifact_store import artifacts_table

    artifact_types = [s.artifact_type for s in schemas]
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa.delete(artifacts_table).where(
                artifacts_table.c.artifact_type.in_(artifact_types)
            )
        )
        await session.commit()
    return result.rowcount  # type: ignore[return-value]


async def ingest_all() -> list[IngestionResult]:
    """Parse all knowledgebase documents into Element records."""
    pipeline = IngestionPipeline()
    results: list[IngestionResult] = []
    for doc in DOCUMENTS:
        result = await pipeline.ingest_file(doc["path"], metadata=doc["metadata"])
        results.append(result)
        print(f"  ✓ ingested {doc['path'].stem:<30} {len(result.elements)} elements")
    return results


# ── Compilation ───────────────────────────────────────────────────────────────


def _schema_for_document(result: IngestionResult, schemas: list[DomainSchema]) -> DomainSchema:
    """Select the best schema for a document based on its metadata."""
    doc_type = result.elements[0].metadata.get("doc_type", "") if result.elements else ""

    # Map doc_type to artifact_type
    type_map = {"policy": "kb_policy", "handbook": "kb_procedure"}
    target_type = type_map.get(doc_type, "kb_procedure")

    for schema in schemas:
        if schema.artifact_type == target_type:
            return schema

    return schemas[0]


def _split_by_section(elements, max_per_chunk: int = 25) -> list[list]:
    """Split elements into chunks by section for individual artifact compilation.

    Each chunk becomes one artifact call. Large sections are further split
    to keep LLM context manageable.
    """
    # First, group elements by their top-level section heading
    sections: dict[str, list] = {}
    for el in elements:
        section_key = el.section_path[0] if el.section_path else "_root"
        sections.setdefault(section_key, []).append(el)

    # Flatten into chunks, splitting oversized sections
    chunks: list[list] = []
    for section_elements in sections.values():
        for i in range(0, len(section_elements), max_per_chunk):
            chunk = section_elements[i : i + max_per_chunk]
            if chunk:
                chunks.append(chunk)

    return chunks


def _normalize_entity_id(entity_id: str) -> str:
    """Normalise an entity_id for within-run dedup.

    Strips document-hierarchy prefixes (text before '›') and drops trailing
    qualifier words that are spurious synonyms of the same concept.
    """
    # Strip hierarchy prefix, e.g. "Security Policy › Incident Response" → "Incident Response"
    if "›" in entity_id:
        entity_id = entity_id.split("›")[-1].strip()
    # Normalise case and drop common trailing qualifiers
    normalized = entity_id.lower().strip()
    for suffix in (" cycle", " process", " standard", " policy"):
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


async def compile_artifacts(
    results: list[IngestionResult],
    schemas: list[DomainSchema],
    router: LLMRouter,
) -> int:
    """Compile artifacts from all ingested documents and store in Postgres.

    Returns the total number of artifacts stored.
    """
    from ake.db.engine import AsyncSessionLocal
    from ake.store.artifact_store import ArtifactStore

    compiler = ArtifactCompiler(router)
    artifact_store = ArtifactStore(AsyncSessionLocal)

    total_stored = 0
    # Track (artifact_type, normalised_entity_id) pairs already stored this run
    # to skip duplicate entities from synonymous headings in the same document.
    seen_entities: set[tuple[str, str]] = set()

    for result in results:
        doc_name = result.source_url.split("/")[-1]
        schema = _schema_for_document(result, schemas)

        # Split elements by section so each artifact covers a coherent topic
        element_chunks = _split_by_section(result.elements, max_per_chunk=25)

        print(f"\n  Compiling {doc_name} ({len(element_chunks)} sections) → {schema.artifact_type}")

        for i, chunk in enumerate(element_chunks):
            section_label = (
                chunk[0].section_path[-1] if chunk[0].section_path else "root"
            )
            try:
                artifact, failed_fields = await compiler.compile(chunk, schema)

                dedup_key = (schema.artifact_type, _normalize_entity_id(artifact.entity_id))
                if dedup_key in seen_entities:
                    print(
                        f"    [{i+1:2d}] {section_label[:50]:<50} "
                        f"SKIPPED duplicate of '{artifact.entity_id}'"
                    )
                    continue

                seen_entities.add(dedup_key)
                await artifact_store.save(artifact)

                status = f"stored ({len(artifact.payload)} fields)"
                if failed_fields:
                    status += f", {len(failed_fields)} nulled: {failed_fields}"
                print(f"    [{i+1:2d}] {section_label[:50]:<50} {status}")
                total_stored += 1

            except Exception as exc:
                print(f"    [{i+1:2d}] {section_label[:50]:<50} FAILED: {exc}")

    return total_stored


# ── MCP registry setup ────────────────────────────────────────────────────────


def register_knowledgebase_types(schemas: list[DomainSchema]) -> None:
    """Register knowledgebase artifact types so MCP agents can discover them.

    Once registered, these types appear in ake_list_artifact_types and
    their schemas are available via ake_describe_schema / ake://schema/{type}.
    """
    for schema in schemas:
        # Build JSON Schema from the DomainSchema FieldSpec entries
        json_schema_properties: dict[str, Any] = {}
        nullable_fields: list[str] = []
        required_fields: list[str] = []

        for field_name, field_spec in schema.fields.items():
            type_map = {
                "str": "string",
                "int": "integer",
                "float": "number",
                "bool": "boolean",
                "date": "string",
            }
            json_type = type_map.get(field_spec.type, "string")
            prop: dict[str, Any] = {
                "type": json_type,
                "description": field_spec.description,
            }

            if field_spec.required:
                required_fields.append(field_name)
            else:
                nullable_fields.append(field_name)

            json_schema_properties[field_name] = prop

        json_schema: dict[str, Any] = {
            "type": "object",
            "properties": json_schema_properties,
        }
        if required_fields:
            json_schema["required"] = required_fields

        register(
            artifact_type=schema.artifact_type,
            domain="knowledgebase",
            description=schema.description,
            json_schema=json_schema,
            source_types=["document"],
            nullable_fields=nullable_fields,
            promoted_filters=["entity_id", "artifact_type", "fiscal_year"],
        )

    print(f"\n  Registered {len(schemas)} artifact types in domain 'knowledgebase'")


# ── Environment check ─────────────────────────────────────────────────────────


def _check_environment() -> None:
    """Verify required environment variables are set."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "✗  DATABASE_URL is not set.\n"
            "   export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake\n"
            "   Then run:  alembic upgrade head"
        )
        sys.exit(1)

    llm_key = os.environ.get("LLM_API_KEY")
    if not llm_key:
        # Check .env.local as fallback (Settings loads this automatically)
        from ake.config import settings

        if not settings.llm_api_key:
            print(
                "✗  LLM_API_KEY is not set.  The compiler needs an LLM to extract artifacts.\n"
                "   export LLM_API_KEY=your-api-key\n"
                "   or set llm_api_key in .env.local\n"
                "\n"
                "   To skip compilation and serve existing artifacts:\n"
                "   uv run python examples/knowledgebase/mcp_server.py --no-compile"
            )
            sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────


async def main() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Acme Corp Knowledge Base — MCP server"
    )
    parser.add_argument(
        "--host", default="127.0.0.1", help="Host to bind the MCP SSE server"
    )
    parser.add_argument(
        "--port", type=int, default=8000, help="Port for the MCP SSE server"
    )
    parser.add_argument(
        "--stdio", 
        action="store_true",
        help="Run STDIO server",
    )
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Skip ingestion and compilation; only start the MCP server (requires "
        "pre-existing artifacts in the database)",
    )
    parser.add_argument(
        "--force-reingest",
        action="store_true",
        help="Delete all existing kb_policy / kb_procedure artifacts from the "
        "database before recompiling. Use this after prompt changes or schema "
        "changes to ensure stale rows don't survive alongside fresh ones.",
    )
    parser.add_argument(
        "--ssl-certfile",
        default=None,
        help="Path to SSL certificate file (enables HTTPS on SSE transport)",
    )
    parser.add_argument(
        "--ssl-keyfile",
        default=None,
        help="Path to SSL private key file",
    )
    parser.add_argument(
        "--ssl-keyfile-password",
        default=None,
        help="Password for the SSL private key file, if encrypted",
    )
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Acme Corp Knowledge Base — MCP Server                   ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    schemas = _build_schemas()

    if not args.no_compile:
        _check_environment()

        if args.force_reingest:
            print("═" * 60)
            print("  --force-reingest: purging existing artifacts")
            print("═" * 60)
            deleted = await purge_knowledgebase_artifacts(schemas)
            print(f"  ✓ {deleted} artifact rows deleted\n")

        # ── Phase 1: Ingestion ─────────────────────────────────────────────
        print("═" * 60)
        print("  Phase 1 — Ingestion (F001)")
        print("═" * 60)
        results = await ingest_all()
        total_elements = sum(len(r.elements) for r in results)
        print(f"\n  {len(results)} documents → {total_elements} total elements")

        # ── Phase 2: Compilation ───────────────────────────────────────────
        print("\n" + "═" * 60)
        print("  Phase 2 — Artifact Compilation (F002)")
        print("═" * 60)

        registry = ToolRegistry()
        router = LLMRouter(registry)

        total_artifacts = await compile_artifacts(results, schemas, router)
        print(f"\n  ✓ {total_artifacts} artifacts compiled and stored")

        # ── Phase 3: Registry ──────────────────────────────────────────────
        print("\n" + "═" * 60)
        print("  Phase 3 — MCP Registry (F011)")
        print("═" * 60)
    else:
        print("  --no-compile: skipping ingestion and compilation")
        print("  (serving pre-existing artifacts from the database)")
        print()

    # Register artifact types (always, so the MCP server discovers them)
    register_knowledgebase_types(schemas)

    # ── Phase 4: MCP Server ────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  Phase 4 — MCP Server")
    print("═" * 60)
    print()
    if not args.stdio:
        print(f"  Starting MCP server on http://{args.host}:{args.port}")
        print(f"  SSE endpoint:  http://{args.host}:{args.port}/sse")
    else:
        print(f"  Starting MCP server with stdio transport")
    print()
    print("  Available tools:")
    print("    • ake_list_artifact_types  — discover 'knowledgebase' domain")
    print("    • ake_describe_schema      — get kb_policy / kb_procedure schemas")
    print("    • ake_query                — ask natural-language questions")
    print("    • ake_get_artifact         — retrieve by entity_id")
    print("    • ake_list_entities        — enumerate all compiled entities")
    print("  Available resources:")
    print("    • ake://domains")
    print("    • ake://domains/knowledgebase")
    print("    • ake://schema/kb_policy")
    print("    • ake://schema/kb_procedure")
    print("    • ake://artifacts/kb_policy/{entity_id}")
    print("    • ake://artifacts/kb_procedure/{entity_id}")
    print("    • ake://citations/{artifact_id}")
    print("    • ake://elements/{doc_id}/{element_id}")
    print()
    print("  Press Ctrl+C to stop.\n")

    return args


if __name__ == "__main__":
    args = asyncio.run(main())
    if not args.stdio:
        run_sse(
            host=args.host,
            port=args.port,
            ssl_certfile=args.ssl_certfile,
            ssl_keyfile=args.ssl_keyfile,
            ssl_keyfile_password=args.ssl_keyfile_password,
        )
    else:
        run_stdio()
