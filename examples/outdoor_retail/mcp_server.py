#!/usr/bin/env python3
"""
Summit & Trail Outdoor Co. — MCP server.

Runs the full AKE pipeline on five structured CSV datasets:
  1. Tabular ingestion (F009)   — CSV → row Element records
  2. Direct tabular compilation — Element rows → typed, cited DomainArtifacts
  3. MCP registry (F011)        — register artifact types for agent discovery
  4. MCP server                 — serve artifacts via SSE / stdio

Unlike the knowledgebase example which uses LLM extraction, this example
uses direct column→field mapping (ADR-009) because the source data is already
structured: every artifact field can be read directly from a column value.

Usage
-----
  export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake
  alembic upgrade head

  uv run python examples/outdoor_retail/mcp_server.py
  uv run python examples/outdoor_retail/mcp_server.py --stdio
  uv run python examples/outdoor_retail/mcp_server.py --no-compile
  uv run python examples/outdoor_retail/mcp_server.py --force-reingest
"""
from __future__ import annotations

import argparse
import asyncio
import os
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

from ake.compiler.artifact import DomainArtifact, DomainSchema, FieldSpec, compute_artifact_id
from ake.compiler.citation import TabularRef
from ake.compiler.verifier import verify_citations
from ake.ingestion.element import Element
from ake.ingestion.pipeline import IngestionPipeline, IngestionResult
from ake.mcp.registry import register
from ake.mcp.server import run_sse, run_stdio

DATA_DIR = Path(__file__).parent / "data"

DATASETS: list[dict[str, Any]] = [
    {
        "path": DATA_DIR / "locations.csv",
        "dataset_name": "summit_trail",
        "metadata": {
            "source_url": "gs://summit-trail-data/retail/locations.csv",
            "acl_principals": ["group:all-employees"],
            "domain": "operations",
            "table": "locations",
        },
    },
    {
        "path": DATA_DIR / "employees.csv",
        "dataset_name": "summit_trail",
        "metadata": {
            "source_url": "gs://summit-trail-data/hr/employees.csv",
            "acl_principals": ["group:hr", "group:management"],
            "domain": "hr",
            "table": "employees",
        },
    },
    {
        "path": DATA_DIR / "products.csv",
        "dataset_name": "summit_trail",
        "metadata": {
            "source_url": "gs://summit-trail-data/retail/products.csv",
            "acl_principals": ["group:all-employees"],
            "domain": "merchandising",
            "table": "products",
        },
    },
    {
        "path": DATA_DIR / "inventory.csv",
        "dataset_name": "summit_trail",
        "metadata": {
            "source_url": "gs://summit-trail-data/retail/inventory.csv",
            "acl_principals": ["group:all-employees"],
            "domain": "operations",
            "table": "inventory",
        },
    },
    {
        "path": DATA_DIR / "sales.csv",
        "dataset_name": "summit_trail",
        "metadata": {
            "source_url": "gs://summit-trail-data/finance/sales.csv",
            "acl_principals": ["group:finance", "group:management"],
            "domain": "finance",
            "table": "sales",
        },
    },
]


# ── Domain schemas ────────────────────────────────────────────────────────────


def _build_schemas() -> dict[str, DomainSchema]:
    return {
        "retail_location": DomainSchema(
            artifact_type="retail_location",
            description=(
                "A retail store location with its city, region, store type, "
                "opening date, and square footage."
            ),
            entity_id_field="location_id",
            fields={
                "location_id": FieldSpec("Unique location code", type="str", required=True),
                "name": FieldSpec("Store display name", type="str", required=True),
                "city": FieldSpec("City the store is in", type="str"),
                "state": FieldSpec("Two-letter US state code", type="str"),
                "region": FieldSpec("Sales region name", type="str"),
                "store_type": FieldSpec("Store tier (flagship/full_service/boutique)", type="str"),
                "opened_date": FieldSpec("Date the store opened (ISO date)", type="str"),
                "sq_ft": FieldSpec("Store floor area in square feet", type="int"),
            },
        ),
        "retail_employee": DomainSchema(
            artifact_type="retail_employee",
            description=(
                "An employee record including department, title, location, "
                "hire date, and salary."
            ),
            entity_id_field="employee_id",
            fields={
                "employee_id": FieldSpec("Unique employee ID", type="str", required=True),
                "first_name": FieldSpec("Employee first name", type="str"),
                "last_name": FieldSpec("Employee last name", type="str"),
                "department": FieldSpec("Department name", type="str"),
                "title": FieldSpec("Job title", type="str"),
                "location_id": FieldSpec("Location code where this employee works", type="str"),
                "hire_date": FieldSpec("ISO hire date", type="str"),
                "salary": FieldSpec("Annual salary in USD", type="float"),
                "status": FieldSpec("Employment status (active/inactive)", type="str"),
            },
        ),
        "retail_product": DomainSchema(
            artifact_type="retail_product",
            description=(
                "A product in the outdoor retail catalog including category, brand, "
                "unit cost, and unit price."
            ),
            entity_id_field="product_id",
            fields={
                "product_id": FieldSpec("Unique product ID", type="str", required=True),
                "sku": FieldSpec("Stock keeping unit code", type="str"),
                "name": FieldSpec("Product display name", type="str", required=True),
                "category": FieldSpec("Product category (Hiking, Camping, etc.)", type="str"),
                "brand": FieldSpec("Brand name", type="str"),
                "unit_cost": FieldSpec("Wholesale cost per unit in USD", type="float"),
                "unit_price": FieldSpec("Retail selling price per unit in USD", type="float"),
                "weight_lbs": FieldSpec("Product weight in pounds", type="float"),
            },
        ),
        "retail_inventory": DomainSchema(
            artifact_type="retail_inventory",
            description=(
                "An inventory position: quantity on hand and reorder metadata "
                "for a product at a specific location."
            ),
            entity_id_field="inventory_id",
            fields={
                "inventory_id": FieldSpec("Unique inventory record ID", type="str", required=True),
                "location_id": FieldSpec("Location code", type="str"),
                "product_id": FieldSpec("Product ID", type="str"),
                "quantity_on_hand": FieldSpec("Units currently in stock", type="int"),
                "quantity_allocated": FieldSpec("Units reserved for pending orders", type="int"),
                "reorder_point": FieldSpec("Quantity that triggers a reorder", type="int"),
                "last_restocked": FieldSpec("ISO date of last restock", type="str"),
            },
        ),
        "retail_sale": DomainSchema(
            artifact_type="retail_sale",
            description=(
                "A sales transaction recording location, product, employee, date, "
                "quantity, and revenue."
            ),
            entity_id_field="sale_id",
            fields={
                "sale_id": FieldSpec("Unique sale transaction ID", type="str", required=True),
                "location_id": FieldSpec("Location where sale occurred", type="str"),
                "product_id": FieldSpec("Product sold", type="str"),
                "employee_id": FieldSpec("Employee who made the sale", type="str"),
                "sale_date": FieldSpec("ISO sale date", type="str"),
                "quantity": FieldSpec("Units sold", type="int"),
                "unit_price": FieldSpec("Selling price per unit", type="float"),
                "revenue": FieldSpec("Total revenue for this transaction", type="float"),
                "discount_pct": FieldSpec("Discount percentage applied", type="float"),
            },
        ),
    }


# ── Direct tabular compiler ───────────────────────────────────────────────────


def _parse_row(element: Element) -> dict[str, str]:
    """Parse 'col: val' pairs from a row element's text."""
    row: dict[str, str] = {}
    for line in element.text.splitlines():
        if ": " in line:
            col, _, val = line.partition(": ")
            row[col.strip()] = val.strip()
    return row


def _coerce(value: str, field_type: str) -> Any:
    """Type-cast a string cell value according to the field spec type."""
    if not value or value.lower() in ("none", "null", ""):
        return None
    try:
        if field_type == "int":
            return int(float(value))
        if field_type == "float":
            return float(value)
        if field_type == "bool":
            return value.lower() in ("true", "1", "yes")
    except (ValueError, TypeError):
        return None
    return value


def compile_tabular_element(
    element: Element,
    schema: DomainSchema,
) -> DomainArtifact | None:
    """Compile one row Element into a DomainArtifact using direct column mapping.

    Uses normalized_values from metadata when available (ADR-009), falling back
    to the raw col:val text. Generates a TabularRef citation for each mapped
    field (ADR-008).
    """
    row = _parse_row(element)
    normalized = element.metadata.get("normalized_values", {})

    # Prefer normalized values where available.
    resolved: dict[str, str] = {**row, **normalized}

    # The entity_id field is required.
    entity_id = row.get(schema.entity_id_field)
    if not entity_id:
        return None

    col_schema_by_name = {
        c["name"]: c
        for c in element.metadata.get("column_schema", [])
    }

    payload: dict[str, Any] = {}
    citations: dict[str, Any] = {}

    for field_name, field_spec in schema.fields.items():
        col_name = field_name  # column name == field name for direct mapping
        raw_val = row.get(col_name)
        if raw_val is None or raw_val == "":
            payload[field_name] = None
            continue

        coerced = _coerce(resolved.get(col_name, raw_val), field_spec.type)
        payload[field_name] = coerced

        dataset = element.metadata.get("source_url", "")
        table = element.metadata.get("table", element.section_path[1] if len(element.section_path) > 1 else "")
        row_id = element.metadata.get("row_id", element.element_id)

        citations[field_name] = TabularRef(
            element_id=element.element_id,
            dataset=dataset,
            table=table,
            row_id=row_id,
            column_name=col_name,
            verbatim_value=raw_val,
        )

    artifact_id = compute_artifact_id(element.doc_id, entity_id, schema.artifact_type)
    acl = element.metadata.get("acl_principals", [])

    artifact = DomainArtifact(
        artifact_id=artifact_id,
        doc_id=element.doc_id,
        entity_id=entity_id,
        artifact_type=schema.artifact_type,
        fiscal_year=None,
        payload=payload,
        field_citations=citations,
        acl_principals=acl,
    )

    artifact, _failed = verify_citations(artifact, [element])
    return artifact


# ── Ingestion ─────────────────────────────────────────────────────────────────


async def ingest_all() -> list[IngestionResult]:
    pipeline = IngestionPipeline()
    results: list[IngestionResult] = []
    for cfg in DATASETS:
        result = await pipeline.ingest_tabular_file(
            cfg["path"],
            metadata=cfg["metadata"],
            dataset_name=cfg["dataset_name"],
        )
        results.append(result)
        print(f"  ✓ {cfg['path'].stem:<15} {len(result.elements):>4} rows")
    return results


# ── Compilation ───────────────────────────────────────────────────────────────

# Map table name to schema key
_TABLE_SCHEMA_MAP: dict[str, str] = {
    "locations": "retail_location",
    "employees": "retail_employee",
    "products": "retail_product",
    "inventory": "retail_inventory",
    "sales": "retail_sale",
}


async def compile_all(
    results: list[IngestionResult],
    schemas: dict[str, DomainSchema],
) -> int:
    from ake.db.engine import AsyncSessionLocal
    from ake.store.artifact_store import ArtifactStore

    artifact_store = ArtifactStore(AsyncSessionLocal)
    total = 0

    for result in results:
        table_name = (
            result.elements[0].metadata.get("table", "")
            if result.elements else ""
        )
        schema_key = _TABLE_SCHEMA_MAP.get(table_name)
        if not schema_key:
            print(f"  ⚠ No schema for table '{table_name}' — skipping")
            continue

        schema = schemas[schema_key]
        stored = 0
        failed = 0

        for element in result.elements:
            artifact = compile_tabular_element(element, schema)
            if artifact is None:
                failed += 1
                continue
            await artifact_store.save(artifact)
            stored += 1

        print(f"  ✓ {table_name:<15} {stored:>4} artifacts stored"
              + (f"  ({failed} skipped)" if failed else ""))
        total += stored

    return total


async def purge_artifacts(schemas: dict[str, DomainSchema]) -> int:
    import sqlalchemy as sa

    from ake.db.engine import AsyncSessionLocal
    from ake.store.artifact_store import artifacts_table

    artifact_types = list(schemas.keys())
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            sa.delete(artifacts_table).where(
                artifacts_table.c.artifact_type.in_(artifact_types)
            )
        )
        await session.commit()
    return result.rowcount  # type: ignore[return-value]


# ── MCP registry ──────────────────────────────────────────────────────────────


def register_retail_types(schemas: dict[str, DomainSchema]) -> None:
    type_map = {"str": "string", "int": "integer", "float": "number", "bool": "boolean"}

    for schema_key, schema in schemas.items():
        props: dict[str, Any] = {}
        required_fields: list[str] = []
        nullable_fields: list[str] = []

        for field_name, field_spec in schema.fields.items():
            props[field_name] = {
                "type": type_map.get(field_spec.type, "string"),
                "description": field_spec.description,
            }
            if field_spec.required:
                required_fields.append(field_name)
            else:
                nullable_fields.append(field_name)

        json_schema: dict[str, Any] = {"type": "object", "properties": props}
        if required_fields:
            json_schema["required"] = required_fields

        register(
            artifact_type=schema.artifact_type,
            domain="outdoor_retail",
            description=schema.description,
            json_schema=json_schema,
            source_types=["tabular"],
            nullable_fields=nullable_fields,
            promoted_filters=["entity_id", "artifact_type"],
        )

    print(f"\n  Registered {len(schemas)} artifact types in domain 'outdoor_retail'")


# ── Environment check ─────────────────────────────────────────────────────────


def _check_environment() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print(
            "✗  DATABASE_URL is not set.\n"
            "   export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake\n"
            "   Then run:  alembic upgrade head"
        )
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────


async def main() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summit & Trail Outdoor Co. — MCP server"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--stdio", action="store_true")
    parser.add_argument(
        "--no-compile",
        action="store_true",
        help="Skip ingestion and compilation; serve existing artifacts only",
    )
    parser.add_argument(
        "--force-reingest",
        action="store_true",
        help="Delete all outdoor_retail artifacts before recompiling",
    )
    args = parser.parse_args()

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Summit & Trail Outdoor Co. — MCP Server                 ║")
    print("╚══════════════════════════════════════════════════════════╝")
    print()

    schemas = _build_schemas()

    if not args.no_compile:
        _check_environment()

        if args.force_reingest:
            print("═" * 60)
            print("  --force-reingest: purging existing artifacts")
            print("═" * 60)
            deleted = await purge_artifacts(schemas)
            print(f"  ✓ {deleted} artifact rows deleted\n")

        # Phase 1: Ingestion
        print("═" * 60)
        print("  Phase 1 — Tabular Ingestion (F009)")
        print("═" * 60)
        results = await ingest_all()
        total_rows = sum(len(r.elements) for r in results)
        print(f"\n  {len(results)} tables → {total_rows} row elements")

        # Phase 2: Direct tabular compilation
        print("\n" + "═" * 60)
        print("  Phase 2 — Direct Column Mapping (ADR-009)")
        print("═" * 60)
        print("  No LLM calls — column name = artifact field name.")
        print()
        total_artifacts = await compile_all(results, schemas)
        print(f"\n  ✓ {total_artifacts} artifacts compiled and stored")

        # Phase 3: Registry
        print("\n" + "═" * 60)
        print("  Phase 3 — MCP Registry (F011)")
        print("═" * 60)
    else:
        print("  --no-compile: serving pre-existing artifacts")
        print()

    register_retail_types(schemas)

    # Phase 4: MCP Server
    print("\n" + "═" * 60)
    print("  Phase 4 — MCP Server")
    print("═" * 60)
    print()
    if not args.stdio:
        print(f"  Starting MCP server on http://{args.host}:{args.port}")
        print(f"  SSE endpoint: http://{args.host}:{args.port}/sse")
    else:
        print("  Starting MCP server with stdio transport")
    print()
    print("  Domain: outdoor_retail")
    print("  Artifact types:")
    for key in schemas:
        print(f"    • {key}")
    print()
    print("  Available tools:")
    print("    • ake_list_artifact_types")
    print("    • ake_describe_schema")
    print("    • ake_query")
    print("    • ake_get_artifact")
    print("    • ake_list_entities")
    print()
    print("  Press Ctrl+C to stop.\n")

    return args


if __name__ == "__main__":
    args = asyncio.run(main())
    if not args.stdio:
        run_sse(host=args.host, port=args.port)
    else:
        run_stdio()
