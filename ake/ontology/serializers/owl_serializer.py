"""Serialize an :class:`~ake.ontology.model.Ontology` to OWL 2 / RDF (Turtle format).

Uses ``rdflib`` (already in the ``ingestion`` dependency group) to build a
proper RDF graph and serialize it to standards-compliant Turtle.

Classes map to ``owl:Class``.
Column properties map to ``owl:DatatypeProperty`` with ``rdfs:domain`` / ``rdfs:range``.
FK relationships map to ``owl:ObjectProperty`` with ``owl:inverseOf`` stubs.
Inferred relationships carry a custom annotation for confidence score.
"""
from __future__ import annotations

from ake.ontology.model import Ontology

try:
    from rdflib import Graph, Literal, Namespace, OWL, RDF, RDFS, URIRef, XSD
    from rdflib.namespace import DCTERMS
except ImportError as exc:
    raise ImportError(
        "OWL serialization requires rdflib.  Run: uv sync --group ingestion"
    ) from exc

_XSD_MAP: dict[str, URIRef] = {
    "xsd:string": XSD.string,
    "xsd:integer": XSD.integer,
    "xsd:decimal": XSD.decimal,
    "xsd:boolean": XSD.boolean,
    "xsd:date": XSD.date,
    "xsd:dateTime": XSD.dateTime,
}


def serialize(ontology: Ontology, base_uri: str | None = None) -> str:
    """Return OWL 2 Turtle for *ontology*.

    Args:
        ontology:  The ontology to serialize.
        base_uri:  Override the ontology URI (default: derived from namespace).
    """
    ns_uri = ontology.namespace
    onto_uri = base_uri or ns_uri.rstrip("#/")

    g = Graph()
    NS = Namespace(ns_uri)
    AKE = Namespace("http://ake.local/vocab#")

    g.bind("", NS)
    g.bind("owl", OWL)
    g.bind("rdf", RDF)
    g.bind("rdfs", RDFS)
    g.bind("xsd", XSD)
    g.bind("dcterms", DCTERMS)
    g.bind("ake", AKE)

    # Ontology declaration
    onto_ref = URIRef(onto_uri)
    g.add((onto_ref, RDF.type, OWL.Ontology))
    g.add((onto_ref, RDFS.label, Literal(ontology.dataset_name)))
    g.add((onto_ref, DCTERMS.created, Literal(ontology.generated_at)))
    g.add((onto_ref, DCTERMS.source, Literal(ontology.source_dir)))

    # Custom annotation properties
    confidence_prop = AKE.inferenceConfidence
    evidence_prop = AKE.inferenceEvidence
    source_col_prop = AKE.sourceColumn
    g.add((confidence_prop, RDF.type, OWL.AnnotationProperty))
    g.add((confidence_prop, RDFS.label, Literal("inference confidence")))
    g.add((evidence_prop, RDF.type, OWL.AnnotationProperty))
    g.add((evidence_prop, RDFS.label, Literal("inference evidence")))
    g.add((source_col_prop, RDF.type, OWL.AnnotationProperty))
    g.add((source_col_prop, RDFS.label, Literal("source column")))

    # OWL Classes — one per table
    class_refs: dict[str, URIRef] = {}
    for cls in ontology.classes:
        cls_ref = NS[cls.name]
        class_refs[cls.name] = cls_ref
        g.add((cls_ref, RDF.type, OWL.Class))
        g.add((cls_ref, RDFS.label, Literal(cls.label)))
        g.add((cls_ref, RDFS.comment, Literal(f"Represents a row in the '{cls.table}' table.")))
        g.add((cls_ref, AKE.sourceTable, Literal(cls.table)))
        g.add((cls_ref, AKE.rowCount, Literal(cls.row_count, datatype=XSD.integer)))

    # OWL DatatypeProperties — one per column
    for cls in ontology.classes:
        cls_ref = class_refs[cls.name]
        for prop in cls.properties:
            prop_ref = NS[f"{cls.name}_{prop.name}"]
            xsd_type = _XSD_MAP.get(prop.datatype, XSD.string)

            g.add((prop_ref, RDF.type, OWL.DatatypeProperty))
            g.add((prop_ref, RDFS.label, Literal(prop.column)))
            g.add((prop_ref, RDFS.domain, cls_ref))
            g.add((prop_ref, RDFS.range, xsd_type))
            g.add((prop_ref, AKE.semanticRole, Literal(prop.semantic_role)))
            if not prop.nullable:
                g.add((prop_ref, OWL.minCardinality, Literal(1, datatype=XSD.nonNegativeInteger)))

    # OWL ObjectProperties — one per inferred FK relationship
    for rel in ontology.relationships:
        src_ref = class_refs.get(rel.domain)
        tgt_ref = class_refs.get(rel.range)
        if not src_ref or not tgt_ref:
            continue

        prop_ref = NS[rel.name]
        g.add((prop_ref, RDF.type, OWL.ObjectProperty))
        g.add((prop_ref, RDFS.label, Literal(rel.label)))
        g.add((prop_ref, RDFS.domain, src_ref))
        g.add((prop_ref, RDFS.range, tgt_ref))
        g.add((prop_ref, confidence_prop, Literal(rel.confidence, datatype=XSD.decimal)))
        g.add((prop_ref, evidence_prop, Literal(rel.evidence)))
        g.add((prop_ref, source_col_prop, Literal(rel.source_column)))

    return g.serialize(format="turtle")
