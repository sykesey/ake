#!/usr/bin/env python3
"""Amorphous ingestion example — auto-discover, model, and export a directory of data.

Point this at any directory of CSV / Parquet / Arrow / document files and it will:
  1. Ingest every file automatically (no schema declaration required)
  2. Infer FK relationships between tables from column naming + value overlap
  3. Build an OWL ontology (classes, data properties, object properties)
  4. Write output to: ontology.yaml, ontology.owl, graph.json, element_tree.json, elements.jsonl

Usage
-----
  uv run python examples/amorphous-ingest/run.py
  uv run python examples/amorphous-ingest/run.py data/ --output out/ --dataset-name acme
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

try:
    import pyarrow  # noqa: F401
except ImportError:
    print(
        "This example requires the 'ingestion' dependency group.\n"
        "Run:  uv sync --group ingestion\n"
        "Then: uv run python examples/amorphous-ingest/run.py"
    )
    sys.exit(1)

# Resolve workspace root so imports work when run from any directory
_HERE = Path(__file__).parent
_ROOT = _HERE.parent.parent
sys.path.insert(0, str(_ROOT))

from ake.ingestion.amorphous_pipeline import AmorphousIngestionResult, ingest_directory
from ake.ontology import serializers
from ake.ontology.builder import build_ontology
from ake.ontology.graph import build_element_tree, build_graph

_RULE = "═" * 52


def _section(title: str) -> None:
    print(f"\n{title}")
    print("─" * len(title))


def _print_summary(result: AmorphousIngestionResult) -> None:
    total = sum(t.row_count for t in result.tables) + sum(
        d.element_count for d in result.documents
    )
    print(f"\n  dataset   : {result.dataset_name}")
    print(f"  source    : {result.source_dir}")
    print(f"  tables    : {len(result.tables)}")
    print(f"  documents : {len(result.documents)}")
    print(f"  elements  : {total}")


def _print_tables(result: AmorphousIngestionResult) -> None:
    _section("Tables")
    for tbl in result.tables:
        role_counts: dict[str, int] = {}
        for col in tbl.columns:
            role_counts[col.semantic_role] = role_counts.get(col.semantic_role, 0) + 1
        roles = ", ".join(f"{r}×{n}" for r, n in sorted(role_counts.items()))
        print(f"  {tbl.name:<20} {tbl.row_count:>4} rows  {len(tbl.columns):>2} cols  [{roles}]")


def _print_relationships(result: AmorphousIngestionResult) -> None:
    _section("Inferred relationships")
    if not result.relationships:
        print("  (none detected)")
        return
    for rel in result.relationships:
        bar = "█" * int(rel.confidence * 10)
        print(
            f"  {rel.source_table}.{rel.source_column:<25}"
            f"→  {rel.target_table}.{rel.target_column:<20}"
            f"  {rel.confidence:.0%}  [{rel.evidence}]  {bar}"
        )


def _write_outputs(
    result: AmorphousIngestionResult,
    output_dir: Path,
) -> None:
    from ake.ontology.builder import build_ontology
    from ake.ontology.graph import build_element_tree, build_graph

    ontology = build_ontology(result)
    graph = build_graph(ontology, result)
    element_tree = build_element_tree(result)

    output_dir.mkdir(parents=True, exist_ok=True)

    _section("Writing output files")

    yaml_path = output_dir / "ontology.yaml"
    yaml_path.write_text(serializers.to_yaml(ontology, result))
    print(f"  {yaml_path}")

    owl_path = output_dir / "ontology.owl"
    owl_path.write_text(serializers.to_owl(ontology))
    print(f"  {owl_path}")

    graph_path = output_dir / "graph.json"
    graph_path.write_text(json.dumps(graph, indent=2))
    print(f"  {graph_path}  ({len(graph['nodes'])} nodes, {len(graph['edges'])} edges)")

    tree_path = output_dir / "element_tree.json"
    tree_path.write_text(json.dumps(element_tree, indent=2))
    print(f"  {tree_path}")

    jsonl_path = output_dir / "elements.jsonl"
    with open(jsonl_path, "w") as fh:
        for el in result.all_elements:
            fh.write(json.dumps({
                "element_id": el.element_id,
                "doc_id": el.doc_id,
                "type": el.type,
                "section_path": el.section_path,
                "row_id": el.metadata.get("row_id", ""),
                "text": el.text,
                "normalized_values": el.metadata.get("normalized_values", {}),
            }) + "\n")
    print(f"  {jsonl_path}  ({len(result.all_elements)} elements)")


async def _run(data_dir: Path, output_dir: Path, dataset_name: str | None) -> None:
    print(_RULE)
    print("  AKE — Amorphous Ingestion Pipeline")
    print(_RULE)

    print(f"\nScanning {data_dir} …")
    result = await ingest_directory(data_dir, dataset_name=dataset_name)
    _print_summary(result)
    _print_tables(result)
    _print_relationships(result)
    _write_outputs(result, output_dir)

    print(f"\n✓  Done — output written to {output_dir}/\n")


def main() -> None:
    default_data = _HERE / "data"
    default_output = _HERE / "output"

    parser = argparse.ArgumentParser(description="AKE amorphous ingestion")
    parser.add_argument(
        "data_dir", nargs="?", default=str(default_data),
        help=f"Directory of data files to ingest (default: {default_data})"
    )
    parser.add_argument(
        "--output", default=str(default_output),
        help=f"Output directory (default: {default_output})"
    )
    parser.add_argument("--dataset-name", default=None, help="Override dataset name")
    args = parser.parse_args()

    asyncio.run(_run(Path(args.data_dir), Path(args.output), args.dataset_name))


if __name__ == "__main__":
    main()
