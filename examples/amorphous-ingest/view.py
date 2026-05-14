#!/usr/bin/env python3
"""
Launch the AKE Amorphous Ingest viewer.

Scans a data directory, runs the full amorphous pipeline (ingestion → ontology
derivation → FK inference), then serves a Starlette/uvicorn server and opens
the browser.

Usage
-----
  uv run python examples/amorphous-ingest/view.py
  uv run python examples/amorphous-ingest/view.py data/ --port 8080 --no-browser
  uv run python examples/amorphous-ingest/view.py data/ --dataset-name acme
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
        "Then: uv run python examples/amorphous-ingest/view.py"
    )
    sys.exit(1)

try:
    import uvicorn
except ImportError:
    print("uvicorn is required.  Run: uv sync --group ingestion")
    sys.exit(1)

_HERE = Path(__file__).parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))

from ake.ingestion.amorphous_pipeline import ingest_directory
from ake.ontology.builder import build_ontology


async def _load(data_dir: Path, dataset_name: str | None):
    print("Ingesting data…")
    result = await ingest_directory(data_dir, dataset_name=dataset_name)
    print(f"  {len(result.tables)} tables, {len(result.all_elements)} elements, "
          f"{len(result.relationships)} relationships")
    print("\nBuilding ontology…")
    ontology = build_ontology(result)
    print(f"  {len(ontology.classes)} classes, {len(ontology.relationships)} object properties")
    return result, ontology


def main() -> None:
    default_data = _HERE / "data"

    parser = argparse.ArgumentParser(description="AKE amorphous ingest viewer")
    parser.add_argument(
        "data_dir", nargs="?", default=str(default_data),
        help=f"Directory of data files (default: {default_data})"
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--dataset-name", default=None)
    args = parser.parse_args()

    data_dir = Path(args.data_dir)
    print("═" * 50)
    print("  AKE — Amorphous Ingest Viewer")
    print("═" * 50)
    print()

    result, ontology = asyncio.run(_load(data_dir, args.dataset_name))

    sys.path.insert(0, str(_HERE))
    from viewer.app import create_app
    app = create_app(result, ontology)

    url = f"http://{args.host}:{args.port}"
    print(f"\n  Viewer → {url}")
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
