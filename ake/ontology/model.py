"""Ontology data model — classes, properties, and relationships produced by the builder."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class OntologyProperty:
    """A single column/field represented as an OWL data property."""
    name: str           # camelCase OWL name, e.g. "employeeId"
    column: str         # source column name, e.g. "employee_id"
    datatype: str       # xsd type string, e.g. "xsd:string"
    semantic_role: str  # entity_id | foreign_key | label | currency | date | ...
    nullable: bool


@dataclass
class OntologyClass:
    """An OWL class derived from an ingested table."""
    name: str                           # PascalCase, e.g. "Employee"
    label: str                          # human label, e.g. "Employee"
    table: str                          # source table name, e.g. "employees"
    doc_id: str
    row_count: int
    properties: list[OntologyProperty] = field(default_factory=list)


@dataclass
class OntologyRelationship:
    """An OWL object property derived from an inferred FK relationship."""
    name: str           # camelCase, e.g. "worksInTeam"
    label: str          # human label, e.g. "works in team"
    domain: str         # OntologyClass.name
    range: str          # OntologyClass.name
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    confidence: float
    evidence: str


@dataclass
class Ontology:
    """Complete ontology for a dataset produced by :func:`~ake.ontology.builder.build_ontology`."""
    dataset_name: str
    source_dir: str
    generated_at: str
    namespace: str
    classes: list[OntologyClass] = field(default_factory=list)
    relationships: list[OntologyRelationship] = field(default_factory=list)

    @property
    def class_map(self) -> dict[str, OntologyClass]:
        return {c.name: c for c in self.classes}

    @property
    def class_by_table(self) -> dict[str, OntologyClass]:
        return {c.table: c for c in self.classes}
