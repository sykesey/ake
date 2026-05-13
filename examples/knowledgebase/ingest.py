#!/usr/bin/env python3
"""
Acme Corp Knowledge Base — AKE ingestion walkthrough.

This script demonstrates the full ingestion pipeline on three company
documents (engineering handbook, HR handbook, security policy). It shows:

  1. Parsing HTML documents into normalised Element records
  2. Content-hash based doc_id (idempotency guarantee)
  3. Section-path extraction from HTML heading hierarchy
  4. ACL metadata propagation into every element
  5. How to use ElementStore to persist elements in Postgres

Usage
-----
  # Parse and explore without a database:
  uv run python examples/knowledgebase/ingest.py

  # Also persist to Postgres:
  DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake \\
      uv run python examples/knowledgebase/ingest.py --store

Prerequisites
-------------
  uv sync --group ingestion     # installs unstructured[pdf,docx]
"""
from __future__ import annotations

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

# ── Dependency check ──────────────────────────────────────────────────────────
try:
    import unstructured  # noqa: F401
except ImportError:
    print(
        "This example requires the 'ingestion' dependency group.\n"
        "Run:  uv sync --group ingestion\n"
        "Then: uv run python examples/knowledgebase/ingest.py"
    )
    sys.exit(1)

from ake.ingestion.element import Element, VALID_ELEMENT_TYPES
from ake.ingestion.pipeline import IngestionPipeline, IngestionResult

DOCS_DIR = Path(__file__).parent / "docs"

# ---------------------------------------------------------------------------
# Document catalogue
# Each entry describes one source document together with the metadata that
# will be merged into every Element produced from that document.  In a real
# deployment you would drive this from a discovery service (SharePoint,
# Confluence, Google Drive), but for this example we keep it static.
# ---------------------------------------------------------------------------
DOCUMENTS: list[dict] = [
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


# ── Display helpers ───────────────────────────────────────────────────────────


def _section_tree(elements: list[Element]) -> list[tuple[int, str]]:
    """Return deduplicated (depth, heading) pairs in document order."""
    seen: set[tuple[str, ...]] = set()
    rows: list[tuple[int, str]] = []
    for el in elements:
        if el.section_path:
            key = tuple(el.section_path)
            if key not in seen:
                seen.add(key)
                rows.append((len(el.section_path), el.section_path[-1]))
    return rows


def _print_result_summary(result: IngestionResult, label: str) -> None:
    type_counts = Counter(el.type for el in result.elements)

    print(f"\n┌─ {label}")
    print(f"│  doc_id  : {result.doc_id[:24]}...")
    print(f"│  elements: {len(result.elements)}")
    print(f"│  types   : {dict(type_counts)}")
    print("│  sections:")
    for depth, heading in _section_tree(result.elements):
        indent = "│    " + "  " * (depth - 1)
        marker = "•" if depth == 1 else "└─"
        print(f"{indent}{marker} {heading}")
    print("└" + "─" * 58)


# ── Demo functions ────────────────────────────────────────────────────────────


def demo_section_filtering(result: IngestionResult, target_section: str) -> None:
    """Print the elements that live under a specific section heading."""
    print(f"\n  Filtering elements where section_path contains '{target_section}':")

    matching = [el for el in result.elements if target_section in el.section_path]
    if not matching:
        print(f"  (no elements found under '{target_section}')")
        return

    for el in matching[:6]:
        path_str = " > ".join(el.section_path) if el.section_path else "(no section)"
        snippet = el.text[:90].replace("\n", " ")
        ellipsis = "…" if len(el.text) > 90 else ""
        print(f"\n  [{el.type:9s}] {path_str}")
        print(f"             {snippet}{ellipsis}")


def demo_idempotency(results_first: list[IngestionResult]) -> None:
    """Show that doc_ids are stable across multiple calls."""
    print()
    print("  doc_id is a content hash — identical for repeated ingestion of the same file:")
    for r in results_first:
        name = r.source_url.split("/")[-1]
        print(f"    {name:30s}  {r.doc_id[:32]}...")


def demo_element_json(element: Element) -> None:
    """Pretty-print an Element as JSON — this is what gets stored in Postgres."""
    record = {
        "doc_id": element.doc_id[:24] + "...",
        "element_id": element.element_id,
        "type": element.type,
        "page": element.page,
        "section_path": element.section_path,
        "text": element.text[:120] + ("…" if len(element.text) > 120 else ""),
        "metadata": {k: v for k, v in element.metadata.items() if k != "raw_type"},
    }
    print(json.dumps(record, indent=2))


def demo_acl_propagation(result: IngestionResult) -> None:
    """Show that ACL principals are present on every element."""
    sample = result.elements[0]
    print(f"  acl_principals : {sample.metadata.get('acl_principals')}")
    print(f"  department     : {sample.metadata.get('department')}")
    print(f"  source_url     : {sample.metadata.get('source_url')}")
    print(f"  (same on all {len(result.elements)} elements in this document)")


# ── Main ──────────────────────────────────────────────────────────────────────


async def main() -> None:
    use_store = "--store" in sys.argv

    print("╔══════════════════════════════════════════════════════════╗")
    print("║  Acme Corp Knowledge Base — AKE Ingestion Walkthrough    ║")
    print("╚══════════════════════════════════════════════════════════╝")

    # ── Set up store (optional) ───────────────────────────────────────────────
    store = None
    if use_store:
        import os

        database_url = os.environ.get("DATABASE_URL")
        if not database_url:
            print("\n✗  --store requires DATABASE_URL to be set.")
            print("   export DATABASE_URL=postgresql+asyncpg://ake:ake@localhost/ake")
            print("   Then run:  alembic upgrade head")
            sys.exit(1)
        from ake.db.engine import AsyncSessionLocal
        from ake.store.element_store import ElementStore

        store = ElementStore(AsyncSessionLocal)
        print(f"\n  Persistence: Postgres  ({database_url[:48]})")
    else:
        print("\n  Persistence: none — pass --store to write elements to Postgres")

    pipeline = IngestionPipeline(store=store)

    # ─────────────────────────────────────────────────────────────────────────
    # Step 1 — Ingest all documents
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 1 — Ingest all knowledge-base documents")
    print("═" * 60)
    print()
    print("  Each document is parsed into Element records with:")
    print("    • A stable doc_id (SHA-256 of raw file bytes)")
    print("    • An element_id unique within the document")
    print("    • A section_path built from the HTML heading hierarchy")
    print("    • Merged ACL metadata propagated to every element")

    results: list[IngestionResult] = []
    for doc_config in DOCUMENTS:
        result = await pipeline.ingest_file(
            doc_config["path"],
            metadata=doc_config["metadata"],
        )
        results.append(result)
        _print_result_summary(result, doc_config["path"].stem)

    total = sum(len(r.elements) for r in results)
    all_types = Counter(el.type for r in results for el in r.elements)
    print(f"\n  ✓ {len(results)} documents → {total} total elements")
    print(f"    type breakdown: {dict(all_types)}")

    # ─────────────────────────────────────────────────────────────────────────
    # Step 2 — Section-path filtering
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 2 — Section-path filtering")
    print("═" * 60)
    print()
    print("  section_path lets you navigate a document semantically.")
    print("  Element ['Code Review Process', 'Reviewer Responsibilities']")
    print("  tells you exactly where in the document hierarchy this content lives,")
    print("  without parsing offsets or page numbers.")

    eng_result = results[0]
    demo_section_filtering(eng_result, "Code Review Process")
    demo_section_filtering(eng_result, "Rollback Procedure")

    # ─────────────────────────────────────────────────────────────────────────
    # Step 3 — Idempotency
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 3 — Idempotency")
    print("═" * 60)
    print()
    print("  Ingesting the same file twice produces the same doc_id.")
    print("  The pipeline will skip re-parsing and re-storing if the store")
    print("  already contains elements for that doc_id.")

    demo_idempotency(results)

    result_again = await pipeline.ingest_file(
        DOCUMENTS[0]["path"], metadata=DOCUMENTS[0]["metadata"]
    )
    assert result_again.doc_id == results[0].doc_id
    assert len(result_again.elements) == len(results[0].elements)
    print()
    print("  Re-ingested engineering-handbook → same doc_id, same element count ✓")

    # ─────────────────────────────────────────────────────────────────────────
    # Step 4 — ACL propagation
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 4 — ACL metadata propagation")
    print("═" * 60)
    print()
    print("  Source ACLs (e.g. Box folder permissions, SharePoint groups)")
    print("  are passed as metadata and merged into every element produced")
    print("  from that document. This is the raw material that F005 uses to")
    print("  enforce row-level security in Postgres.")
    print()
    demo_acl_propagation(results[0])

    # ─────────────────────────────────────────────────────────────────────────
    # Step 5 — Element record (JSON)
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  STEP 5 — What an Element record looks like")
    print("═" * 60)
    print()
    print("  This is the normalised record stored in the 'elements' table.")
    print("  The compiler (F002) reads these records and extracts typed,")
    print("  cited artifacts — without touching the original HTML again.")
    print()

    # Pick an interesting element (one with a nested section_path)
    interesting = next(
        (el for el in eng_result.elements if len(el.section_path) >= 2 and el.type == "paragraph"),
        eng_result.elements[2],
    )
    demo_element_json(interesting)

    # ─────────────────────────────────────────────────────────────────────────
    # Wrap up
    # ─────────────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    print("  Done")
    print("═" * 60)

    if store:
        print(f"\n  ✓ All {total} elements written to Postgres.")
        print("    Retrieve them with ElementStore.get_by_doc_id(doc_id).")
        print("    Re-run this script — the store will skip re-parsing (idempotent).")
    else:
        print("\n  Next steps:")
        print("    • Run 'alembic upgrade head' to create the elements table")
        print("    • Re-run with --store to persist elements to Postgres")
        print("    • Implement F002 (artifact compilation) to extract typed facts")
        print("      from these elements using the LLMRouter")


if __name__ == "__main__":
    asyncio.run(main())
