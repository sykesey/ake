#!/usr/bin/env python3
"""
Launch the AKE knowledge-graph viewer.

Ingests the example knowledge-base documents in-memory, then starts a
local Starlette/uvicorn server and opens the browser automatically.

Usage
-----
  uv run python examples/knowledgebase/view.py
  uv run python examples/knowledgebase/view.py --port 8080 --no-browser
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

try:
    import unstructured  # noqa: F401
except ImportError:
    print(
        "This viewer requires the 'ingestion' dependency group.\n"
        "Run:  uv sync --group ingestion\n"
        "Then: uv run python examples/knowledgebase/view.py"
    )
    sys.exit(1)

try:
    import uvicorn
except ImportError:
    print(
        "uvicorn is required to run the viewer.\n"
        "Run:  uv sync --group ingestion"
    )
    sys.exit(1)

from ake.ingestion.pipeline import IngestionPipeline, IngestionResult

DOCS_DIR = Path(__file__).parent / "docs"

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


async def ingest_all() -> list[IngestionResult]:
    pipeline = IngestionPipeline()
    results: list[IngestionResult] = []
    for doc in DOCUMENTS:
        result = await pipeline.ingest_file(doc["path"], metadata=doc["metadata"])
        results.append(result)
        total = len(result.elements)
        print(f"  ✓ {doc['path'].stem:<30} {total} elements")
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="AKE knowledge-graph viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true", help="Don't open browser automatically")
    args = parser.parse_args()

    print("AKE Knowledge Graph Viewer")
    print("══════════════════════════")
    print()
    print("Ingesting documents…")
    results = asyncio.run(ingest_all())
    total = sum(len(r.elements) for r in results)
    print(f"\n  {len(results)} documents, {total} elements")

    # viewer/ has __init__.py; make its parent importable without installing examples/
    sys.path.insert(0, str(Path(__file__).parent))
    from viewer.app import create_app
    app = create_app(results)

    url = f"http://{args.host}:{args.port}"
    print(f"\n  Viewer → {url}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        # Open after a short delay so uvicorn is ready
        import threading
        def _open():
            import time
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
