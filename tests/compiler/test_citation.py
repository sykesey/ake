"""Tests for the polymorphic Citation model (ADR-008)."""
from __future__ import annotations

import json

import pytest
from pydantic import TypeAdapter

from ake.compiler.citation import Citation, DocumentRef, GraphRef, TabularRef


_adapter: TypeAdapter[Citation] = TypeAdapter(Citation)


def _rt(obj: dict) -> Citation:
    """Round-trip: dict → Citation → dict."""
    return _adapter.validate_python(obj)


class TestDocumentRef:
    def test_round_trip(self):
        raw = {
            "source_type": "document",
            "element_id": "abc123",
            "char_start": 10,
            "char_end": 40,
            "verbatim_span": "revenue was $1.2 billion",
        }
        ref = _rt(raw)
        assert isinstance(ref, DocumentRef)
        assert ref.element_id == "abc123"
        assert ref.char_start == 10
        assert ref.char_end == 40
        assert ref.verbatim_span == "revenue was $1.2 billion"

    def test_json_serialisation(self):
        ref = DocumentRef(
            element_id="x",
            char_start=0,
            char_end=5,
            verbatim_span="hello",
        )
        data = json.loads(ref.model_dump_json())
        assert data["source_type"] == "document"
        assert data["verbatim_span"] == "hello"

    def test_discriminator_required(self):
        with pytest.raises(Exception):
            _rt({"element_id": "x", "char_start": 0, "char_end": 5, "verbatim_span": "hi"})


class TestTabularRef:
    def test_round_trip(self):
        raw = {
            "source_type": "tabular",
            "element_id": "row42",
            "dataset": "financials",
            "table": "income_statement",
            "row_id": "2024-Q1",
            "column_name": "revenue",
            "verbatim_value": "1200",
        }
        ref = _rt(raw)
        assert isinstance(ref, TabularRef)
        assert ref.column_name == "revenue"
        assert ref.verbatim_value == "1200"


class TestGraphRef:
    def test_round_trip_node(self):
        raw = {
            "source_type": "graph",
            "element_id": "node01",
            "graph_id": "kg_acme",
            "node_id": "entity:apple",
            "edge_id": None,
            "property_name": "market_cap",
        }
        ref = _rt(raw)
        assert isinstance(ref, GraphRef)
        assert ref.node_id == "entity:apple"
        assert ref.edge_id is None

    def test_optional_fields_default_none(self):
        raw = {
            "source_type": "graph",
            "element_id": "n1",
            "graph_id": "kg",
        }
        ref = _rt(raw)
        assert ref.node_id is None
        assert ref.edge_id is None
        assert ref.property_name is None


class TestDiscriminator:
    def test_wrong_source_type_raises(self):
        with pytest.raises(Exception):
            _rt({"source_type": "audio", "element_id": "x"})

    def test_each_variant_resolves(self):
        cases = [
            {"source_type": "document", "element_id": "a", "char_start": 0, "char_end": 1, "verbatim_span": "x"},
            {"source_type": "tabular", "element_id": "b", "dataset": "d", "table": "t", "row_id": "r", "column_name": "c", "verbatim_value": "v"},
            {"source_type": "graph", "element_id": "c", "graph_id": "g"},
        ]
        types = [DocumentRef, TabularRef, GraphRef]
        for raw, expected_type in zip(cases, types):
            assert isinstance(_rt(raw), expected_type)
