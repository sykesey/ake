"""Ontology serializers — YAML and OWL Turtle."""
from __future__ import annotations

from ake.ingestion.amorphous_pipeline import AmorphousIngestionResult
from ake.ontology.model import Ontology
from ake.ontology.serializers import owl_serializer, yaml_serializer


def to_yaml(ontology: Ontology, result: AmorphousIngestionResult | None = None) -> str:
    """Serialize *ontology* to a human-readable YAML string."""
    return yaml_serializer.serialize(ontology, result)


def to_owl(ontology: Ontology, base_uri: str | None = None) -> str:
    """Serialize *ontology* to OWL 2 / RDF Turtle string."""
    return owl_serializer.serialize(ontology, base_uri)
