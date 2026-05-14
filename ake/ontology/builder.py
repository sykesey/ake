"""Build an :class:`~ake.ontology.model.Ontology` from an :class:`~ake.ingestion.amorphous_pipeline.AmorphousIngestionResult`."""
from __future__ import annotations

from datetime import datetime, timezone

from ake.ingestion.amorphous_pipeline import AmorphousIngestionResult, InferredRelationship
from ake.ontology.model import Ontology, OntologyClass, OntologyProperty, OntologyRelationship

_DEFAULT_NAMESPACE = "http://ake.local/ontology/{dataset}#"

# pyarrow type string → XSD type
_PA_TO_XSD: dict[str, str] = {
    "string": "xsd:string",
    "large_string": "xsd:string",
    "utf8": "xsd:string",
    "int8": "xsd:integer",
    "int16": "xsd:integer",
    "int32": "xsd:integer",
    "int64": "xsd:integer",
    "uint8": "xsd:integer",
    "uint16": "xsd:integer",
    "uint32": "xsd:integer",
    "uint64": "xsd:integer",
    "float": "xsd:decimal",
    "float16": "xsd:decimal",
    "float32": "xsd:decimal",
    "float64": "xsd:decimal",
    "double": "xsd:decimal",
    "bool": "xsd:boolean",
    "boolean": "xsd:boolean",
    "date32[day]": "xsd:date",
    "date64[ms]": "xsd:date",
    "timestamp[s]": "xsd:dateTime",
    "timestamp[ms]": "xsd:dateTime",
    "timestamp[us]": "xsd:dateTime",
    "timestamp[ns]": "xsd:dateTime",
}


def _to_pascal_case(snake: str) -> str:
    """employees → Employee, project_budget → ProjectBudget"""
    parts = snake.rstrip("s").split("_") if snake.endswith("s") else snake.split("_")
    return "".join(p.title() for p in parts)


def _to_camel_case(snake: str) -> str:
    """employee_id → employeeId, first_name → firstName"""
    parts = snake.split("_")
    return parts[0] + "".join(p.title() for p in parts[1:])


def _xsd_type(pa_type: str) -> str:
    # Strip timestamp parameters: "timestamp[us, tz=UTC]" → "timestamp[us]"
    base = pa_type.split(",")[0].rstrip("]") + "]" if "[" in pa_type else pa_type
    return _PA_TO_XSD.get(base, _PA_TO_XSD.get(pa_type, "xsd:string"))


def _rel_name(src_col: str, tgt_class: str) -> str:
    """lead_employee_id → Employee  → leadEmployee"""
    base = src_col[:-3]  # strip "_id"
    camel = _to_camel_case(base)
    return camel


def _rel_label(src_col: str) -> str:
    return src_col[:-3].replace("_", " ")


def build_ontology(
    result: AmorphousIngestionResult,
    namespace: str | None = None,
) -> Ontology:
    """Derive an OWL-ready :class:`~ake.ontology.model.Ontology` from ingestion results.

    Each table becomes an :class:`~ake.ontology.model.OntologyClass`; each column becomes an
    :class:`~ake.ontology.model.OntologyProperty`; each inferred FK relationship becomes an
    :class:`~ake.ontology.model.OntologyRelationship`.
    """
    ns = (namespace or _DEFAULT_NAMESPACE).format(dataset=result.dataset_name)

    classes: list[OntologyClass] = []
    for tbl in result.tables:
        props = [
            OntologyProperty(
                name=_to_camel_case(col.name),
                column=col.name,
                datatype=_xsd_type(col.pa_type),
                semantic_role=col.semantic_role,
                nullable=col.nullable,
            )
            for col in tbl.columns
        ]
        classes.append(OntologyClass(
            name=_to_pascal_case(tbl.name),
            label=_to_pascal_case(tbl.name),
            table=tbl.name,
            doc_id=tbl.result.doc_id,
            row_count=tbl.row_count,
            properties=props,
        ))

    class_by_table = {c.table: c for c in classes}

    relationships: list[OntologyRelationship] = []
    for rel in result.relationships:
        src_cls = class_by_table.get(rel.source_table)
        tgt_cls = class_by_table.get(rel.target_table)
        if not src_cls or not tgt_cls:
            continue
        relationships.append(OntologyRelationship(
            name=_rel_name(rel.source_column, tgt_cls.name),
            label=_rel_label(rel.source_column),
            domain=src_cls.name,
            range=tgt_cls.name,
            source_table=rel.source_table,
            source_column=rel.source_column,
            target_table=rel.target_table,
            target_column=rel.target_column,
            confidence=rel.confidence,
            evidence=rel.evidence,
        ))

    return Ontology(
        dataset_name=result.dataset_name,
        source_dir=str(result.source_dir),
        generated_at=datetime.now(timezone.utc).isoformat(),
        namespace=ns,
        classes=classes,
        relationships=relationships,
    )
