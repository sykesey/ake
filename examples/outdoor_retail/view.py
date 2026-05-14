#!/usr/bin/env python3
"""
Launch the Summit & Trail Outdoor Co. data viewer.

Ingests the five CSV datasets in-memory, builds a tabular element graph,
then serves a local Starlette/uvicorn server and opens the browser.

Usage
-----
  uv run python examples/outdoor_retail/view.py
  uv run python examples/outdoor_retail/view.py --port 8080 --no-browser
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import webbrowser
from pathlib import Path

try:
    import pyarrow  # noqa: F401
except ImportError:
    print(
        "This viewer requires the 'ingestion' dependency group.\n"
        "Run:  uv sync --group ingestion\n"
        "Then: uv run python examples/outdoor_retail/view.py"
    )
    sys.exit(1)

try:
    import uvicorn
except ImportError:
    print("uvicorn is required.  Run: uv sync --group ingestion")
    sys.exit(1)

from ake.ingestion.pipeline import IngestionPipeline, IngestionResult

DATA_DIR = Path(__file__).parent / "data"

DATASETS = [
    {"path": DATA_DIR / "locations.csv",  "dataset_name": "summit_trail", "metadata": {"source_url": "gs://summit-trail-data/retail/locations.csv",  "acl_principals": ["group:all-employees"], "domain": "operations",     "table": "locations"}},
    {"path": DATA_DIR / "employees.csv",  "dataset_name": "summit_trail", "metadata": {"source_url": "gs://summit-trail-data/hr/employees.csv",       "acl_principals": ["group:hr"],            "domain": "hr",             "table": "employees"}},
    {"path": DATA_DIR / "products.csv",   "dataset_name": "summit_trail", "metadata": {"source_url": "gs://summit-trail-data/retail/products.csv",   "acl_principals": ["group:all-employees"], "domain": "merchandising",  "table": "products"}},
    {"path": DATA_DIR / "inventory.csv",  "dataset_name": "summit_trail", "metadata": {"source_url": "gs://summit-trail-data/retail/inventory.csv",  "acl_principals": ["group:all-employees"], "domain": "operations",     "table": "inventory"}},
    {"path": DATA_DIR / "sales.csv",      "dataset_name": "summit_trail", "metadata": {"source_url": "gs://summit-trail-data/finance/sales.csv",     "acl_principals": ["group:finance"],       "domain": "finance",        "table": "sales"}},
]


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


def main() -> None:
    parser = argparse.ArgumentParser(description="Summit & Trail data viewer")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    print("Summit & Trail Outdoor Co. — Data Viewer")
    print("═" * 42)
    print()
    print("Ingesting tables…")
    results = asyncio.run(ingest_all())
    total = sum(len(r.elements) for r in results)
    print(f"\n  {len(results)} tables, {total} row elements\n")

    sys.path.insert(0, str(Path(__file__).parent))
    from viewer.app import create_app
    app = create_app(results)

    url = f"http://{args.host}:{args.port}"
    print(f"  Viewer → {url}")
    print("  Press Ctrl+C to stop.\n")

    if not args.no_browser:
        import threading
        def _open():
            import time
            time.sleep(0.8)
            webbrowser.open(url)
        threading.Thread(target=_open, daemon=True).start()

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
