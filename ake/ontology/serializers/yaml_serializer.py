"""Serialize an :class:`~ake.ontology.model.Ontology` to YAML.

No external YAML library is required — the output is hand-formatted
for readability.  The format captures dataset metadata, per-table
column schemas with semantic roles, inferred relationships, and the
OWL class/property model.
"""
from __future__ import annotations

from ake.ingestion.amorphous_pipeline import AmorphousIngestionResult
from ake.ontology.model import Ontology


def _ys(value: object) -> str:
    """Format a scalar value as a YAML flow scalar, quoting when necessary."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    s = str(value)
    needs_quote = (
        not s
        or s[0] in "\"'[]{},#&*?|<>=!%@`\\"
        or s[0].isdigit()
        or s[0] == "-"
        or s[0] == ":"
        or s.lower() in ("true", "false", "null", "yes", "no", "on", "off")
        or ":" in s
        or "#" in s
    )
    return f'"{s}"' if needs_quote else s


def serialize(ontology: Ontology, result: AmorphousIngestionResult | None = None) -> str:
    """Return a YAML string representing the full ontology and dataset summary."""
    lines: list[str] = []

    lines += [
        f"# AKE Amorphous Ontology — {ontology.dataset_name}",
        f"# Generated: {ontology.generated_at}",
        "",
        "dataset:",
        f"  name: {_ys(ontology.dataset_name)}",
        f"  source_dir: {_ys(ontology.source_dir)}",
        f"  generated_at: {_ys(ontology.generated_at)}",
    ]

    if result is not None:
        total_els = sum(t.row_count for t in result.tables) + sum(
            d.element_count for d in result.documents
        )
        lines += [
            "  stats:",
            f"    tables: {len(result.tables)}",
            f"    documents: {len(result.documents)}",
            f"    total_elements: {total_els}",
            f"    relationships: {len(result.relationships)}",
        ]

    # Per-table column schemas
    lines += ["", "tables:"]
    for cls in ontology.classes:
        tbl_result = None
        if result is not None:
            tbl_info = next((t for t in result.tables if t.name == cls.table), None)
            tbl_result = tbl_info.result if tbl_info else None

        lines += [
            f"  {cls.table}:",
            f"    class: {_ys(cls.name)}",
            f"    doc_id: {_ys(cls.doc_id)}",
            f"    row_count: {cls.row_count}",
        ]
        if tbl_result:
            lines.append(f"    source: {_ys(tbl_result.source_url)}")
        lines.append("    columns:")
        for prop in cls.properties:
            lines += [
                f"      - name: {_ys(prop.column)}",
                f"        owl_name: {_ys(prop.name)}",
                f"        type: {_ys(prop.datatype)}",
                f"        nullable: {_ys(prop.nullable)}",
                f"        semantic_role: {_ys(prop.semantic_role)}",
            ]

    # Inferred FK relationships
    lines += ["", "relationships:"]
    if ontology.relationships:
        for rel in ontology.relationships:
            lines += [
                f"  - source_table: {_ys(rel.source_table)}",
                f"    source_column: {_ys(rel.source_column)}",
                f"    target_table: {_ys(rel.target_table)}",
                f"    target_column: {_ys(rel.target_column)}",
                f"    confidence: {rel.confidence}",
                f"    evidence: {_ys(rel.evidence)}",
                f"    owl_property: {_ys(rel.name)}",
            ]
    else:
        lines.append("  []")

    # OWL model summary
    lines += [
        "",
        "ontology:",
        f"  namespace: {_ys(ontology.namespace)}",
        "  classes:",
    ]
    for cls in ontology.classes:
        lines.append(f"    - name: {_ys(cls.name)}")
        lines.append(f"      label: {_ys(cls.label)}")
        lines.append(f"      table: {_ys(cls.table)}")
        lines.append("      data_properties:")
        for prop in cls.properties:
            lines += [
                f"        - {_ys(prop.name)}: {_ys(prop.datatype)}  # {prop.semantic_role}",
            ]

    lines += ["", "  object_properties:"]
    if ontology.relationships:
        for rel in ontology.relationships:
            lines += [
                f"    - name: {_ys(rel.name)}",
                f"      label: {_ys(rel.label)}",
                f"      domain: {_ys(rel.domain)}",
                f"      range: {_ys(rel.range)}",
                f"      source_column: {_ys(rel.source_column)}",
                f"      confidence: {rel.confidence}",
            ]
    else:
        lines.append("    []")

    lines.append("")
    return "\n".join(lines)
