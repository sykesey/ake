#!/usr/bin/env python3
"""
Summit & Trail Outdoor Co. — AKE tabular ingestion walkthrough.

Demonstrates the F009 tabular ingestion pipeline on five structured CSV
datasets representing a simulated outdoor-retail business:

  employees   — 20 staff records across 5 store locations
  locations   — 5 retail store locations
  products    — 30 products across 5 outdoor categories
  inventory   — stock levels per location per product
  sales       — 110 sales transactions from Q1 2025

Key behaviours shown:
  1. Tabular doc_id hashes (source_uri, schema_fingerprint, content_hash)
  2. Each row → one Element(type="row") with "col: val" text pairs
  3. Schema-aware normalizer: dates/numerics in metadata.normalized_values
  4. Column schema in metadata.column_schema
  5. Idempotency: re-ingesting the same file yields the same doc_id
  6. Sparse row detection (no null-only rows emitted)

Usage
-----
  # Parse and explore without a database:
  uv run python examples/outdoor_retail/ingest.py

  # Also persist to Postgres:
  DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake \\
      uv run python examples/outdoor_retail/ingest.py --store

Prerequisites
-------------
  uv sync --group ingestion     # installs pyarrow
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

try:
    import pyarrow  # noqa: F401
except ImportError:
    print(
        "This example requires the 'ingestion' dependency group.\n"
        "Run:  uv sync --group ingestion\n"
        "Then: uv run python examples/outdoor_retail/ingest.py"
    )
    sys.exit(1)

from ake.ingestion.element import Element
from ake.ingestion.pipeline import IngestionPipeline, IngestionResult

DATA_DIR = Path(__file__).parent / "data"

# ---------------------------------------------------------------------------
# Dataset catalogue: one entry per CSV table.
# metadata drives section context and ACL propagation.
# ---------------------------------------------------------------------------
DATASETS: list[dict] = [
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


# ── Display helpers ───────────────────────────────────────────────────────────


def _print_result_summary(result: IngestionResult, label: str) -> None:
    el = result.elements
    col_schema = el[0].metadata.get("column_schema", []) if el else []
    col_names = [c["name"] for c in col_schema]
    nv_count = sum(1 for e in el if e.metadata.get("normalized_values"))
    print(f"\n┌─ {label}")
    print(f"│  doc_id     : {result.doc_id[:28]}...")
    print(f"│  rows       : {len(el)}")
    print(f"│  columns    : {len(col_names)}  → {', '.join(col_names[:6])}"
          + ("…" if len(col_names) > 6 else ""))
    print(f"│  normalized : {nv_count}/{len(el)} rows have pre-normalized values")
    print("└" + "─" * 58)


def _demo_element_json(element: Element, max_cols: int = 6) -> None:
    """Pretty-print one row Element as JSON."""
    col_schema = element.metadata.get("column_schema", [])
    nv = element.metadata.get("normalized_values", {})
    record = {
        "doc_id": element.doc_id[:20] + "...",
        "element_id": element.element_id,
        "type": element.type,
        "section_path": element.section_path,
        "text": element.text[:240] + ("…" if len(element.text) > 240 else ""),
        "metadata": {
            "source_url": element.metadata.get("source_url"),
            "table": element.metadata.get("table"),
            "row_id": element.metadata.get("row_id"),
            "column_count": len(col_schema),
            "normalized_values": nv,
        },
    }
    print(json.dumps(record, indent=2, default=str))


def _demo_column_filtering(result: IngestionResult, col: str, value: str) -> None:
    """Show elements where a specific column equals a value."""
    matches = [
        el for el in result.elements
        if f"{col}: {value}" in el.text
    ]
    print(f"\n  Rows where {col} = '{value}':")
    for el in matches[:4]:
        lines = el.text.splitlines()
        row_preview = " | ".join(lines[:4]) + ("…" if len(lines) > 4 else "")
        print(f"    [{el.element_id}] {row_preview}")
    if not matches:
        print(f"    (no rows matched '{col}: {value}')")


def _demo_normalized_values(result: IngestionResult) -> None:
    """Show one element that has normalized date/currency values."""
    with_nv = [el for el in result.elements if el.metadata.get("normalized_values")]
    if not with_nv:
        print("  No normalized values found in this table.")
        return
    sample = with_nv[0]
    nv = sample.metadata["normalized_values"]
    print(f"\n  Sample row element_id={sample.element_id}")
    print(f"  Raw text (first 3 lines):")
    for line in sample.text.splitlines()[:3]:
        print(f"    {line}")
    print(f"  Pre-normalized values (metadata.normalized_values):")
    for k, v in list(nv.items())[:5]:
        print(f"    {k}: {v}")


# ── Main ──────────────────────────────────────────────────────────────────────


async def main() -> None:
    use_store = "--store" in sys.argv

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Summit & Trail Outdoor Co. — Tabular Ingestion          ║")
    print("╚══════════════════════════════════════════════════════════╝")

    store = None
    if use_store:
        import os

        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            print("\n✗  --store requires DATABASE_URL to be set.")
            print("   export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake")
            sys.exit(1)
        from ake.db.engine import AsyncSessionLocal
        from ake.store.element_store import ElementStore

        store = ElementStore(AsyncSessionLocal)
        print(f"\n  Persistence: Postgres  ({database_url[:48]})")
    else:
        print("\n  Persistence: none — pass --store to write elements to Postgres")

    pipeline = IngestionPipeline(store=store)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1 — Ingest all tables
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 1 — Ingest all tables (F009 tabular pipeline)")
    print("═" * 60)
    print()
    print("  Each CSV row → one Element(type='row'):")
    print("    • doc_id = SHA-256(source_uri + schema_fingerprint + content_hash)")
    print("    • element.text = 'col: val\\ncol: val' (verbatim, for citation)")
    print("    • metadata.column_schema = [{name, type, nullable}, …]")
    print("    • metadata.normalized_values = {col: normalized_str, …}")

    results: list[IngestionResult] = []
    for cfg in DATASETS:
        result = await pipeline.ingest_tabular_file(
            cfg["path"],
            metadata=cfg["metadata"],
            dataset_name=cfg["dataset_name"],
        )
        results.append(result)
        _print_result_summary(result, cfg["path"].stem)

    total_rows = sum(len(r.elements) for r in results)
    print(f"\n  ✓ {len(results)} tables → {total_rows} total row elements")

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2 — Column-value filtering
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 2 — Column-value filtering (the 'col: val' text format)")
    print("═" * 60)
    print()
    print("  Because element.text uses 'col: val' pairs, downstream code can")
    print("  filter elements by column value without a schema-aware query layer.")
    print("  The same format enables TabularRef citation verification (ADR-008).")

    products_result = next(r for r in results if "products" in r.source_url)
    employees_result = next(r for r in results if "employees" in r.source_url)

    _demo_column_filtering(products_result, "category", "Hiking")
    _demo_column_filtering(employees_result, "department", "Outdoor Education")

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3 — Pre-normalized values
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 3 — Schema-aware normalization")
    print("═" * 60)
    print()
    print("  Numeric columns (salary, revenue, unit_price) and date columns")
    print("  (hire_date, sale_date, last_restocked) are pre-normalized at ingest")
    print("  and stored in metadata.normalized_values — so the compiler's")
    print("  direct-mapping path reads them without an LLM call (ADR-009).")

    sales_result = next(r for r in results if "sales" in r.source_url)
    _demo_normalized_values(employees_result)
    print()
    _demo_normalized_values(sales_result)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 4 — Idempotency
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 4 — Idempotency")
    print("═" * 60)
    print()
    print("  Re-ingesting an unchanged CSV produces the same doc_id and the same")
    print("  element set.  A schema change (column added or renamed) produces a")
    print("  different doc_id, triggering full re-ingestion and re-compilation.")

    reingest = await pipeline.ingest_tabular_file(
        DATASETS[2]["path"],
        metadata=DATASETS[2]["metadata"],
        dataset_name=DATASETS[2]["dataset_name"],
    )
    products_orig = results[2]
    assert reingest.doc_id == products_orig.doc_id
    assert len(reingest.elements) == len(products_orig.elements)
    print()
    print(f"  Re-ingested products.csv → doc_id unchanged ✓")
    print(f"    {reingest.doc_id[:40]}...")

    # ─────────────────────────────────────────────────────────────────────────
    # Step 5 — Full element record
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 5 — What a row Element looks like (the 'row' type)")
    print("═" * 60)
    print()
    print("  This is the normalised record stored in the 'elements' table.")
    print("  The compiler reads these to build typed, cited DomainArtifacts.")
    print()

    sample = next(
        el for el in products_result.elements if "Hiking" in el.text
    )
    _demo_element_json(sample)

    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  Done")
    print("═" * 60)

    if store:
        print(f"\n  ✓ All {total_rows} row elements written to Postgres.")
    else:
        print("\n  Next steps:")
        print("    • Run 'alembic upgrade head' to create the elements table")
        print("    • Re-run with --store to persist to Postgres")
        print("    • Run mcp_server.py to compile artifacts and serve via MCP")
        print("    • Run view.py to browse the tabular element graph")


if __name__ == "__main__":
    asyncio.run(main())
