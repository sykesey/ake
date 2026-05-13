"""Tests for citation verification (F003)."""
from __future__ import annotations

from ake.compiler.artifact import DomainArtifact, compute_artifact_id
from ake.compiler.citation import DocumentRef, GraphRef, TabularRef
from ake.compiler.verifier import verify_citations
from ake.ingestion.element import Element


def _element(element_id: str, text: str, doc_id: str = "doc1") -> Element:
    return Element(
        doc_id=doc_id,
        element_id=element_id,
        type="paragraph",
        text=text,
        page=1,
        section_path=["Section"],
    )


def _artifact(payload: dict, citations: dict) -> DomainArtifact:
    return DomainArtifact(
        artifact_id=compute_artifact_id("doc1", "entity1", "policy"),
        doc_id="doc1",
        entity_id="entity1",
        artifact_type="policy",
        fiscal_year=None,
        payload=payload,
        field_citations=citations,
        acl_principals=[],
    )


class TestDocumentRefVerification:
    def test_valid_citation_passes(self):
        el = _element("e1", "Employees receive 25 days of annual leave.")
        span = "25 days of annual leave"
        pos = el.text.index(span)
        art = _artifact(
            {"annual_days": 25},
            {"annual_days": DocumentRef(element_id="e1", char_start=pos, char_end=pos + len(span), verbatim_span=span)},
        )
        result, failed = verify_citations(art, [el])
        assert failed == []
        assert result.payload["annual_days"] == 25

    def test_element_not_found_nulls_field(self):
        el = _element("e1", "Some text.")
        art = _artifact(
            {"annual_days": 25},
            {"annual_days": DocumentRef(element_id="missing", char_start=0, char_end=2, verbatim_span="So")},
        )
        result, failed = verify_citations(art, [el])
        assert "annual_days" in failed
        assert result.payload["annual_days"] is None
        assert "annual_days" not in result.field_citations

    def test_span_not_in_text_nulls_field(self):
        el = _element("e1", "Employees receive 25 days of annual leave.")
        art = _artifact(
            {"annual_days": 30},
            {"annual_days": DocumentRef(element_id="e1", char_start=0, char_end=5, verbatim_span="30 days")},
        )
        result, failed = verify_citations(art, [el])
        assert "annual_days" in failed
        assert result.payload["annual_days"] is None

    def test_null_value_skips_verification(self):
        el = _element("e1", "Some text.")
        art = _artifact({"annual_days": None}, {})
        result, failed = verify_citations(art, [el])
        assert failed == []
        assert result.payload["annual_days"] is None

    def test_missing_citation_for_nonnull_field_nulls_it(self):
        el = _element("e1", "25 days leave.")
        art = _artifact({"annual_days": 25}, {})  # no citation provided
        result, failed = verify_citations(art, [el])
        assert "annual_days" in failed
        assert result.payload["annual_days"] is None

    def test_multiple_fields_partial_failure(self):
        el = _element("e1", "25 days annual leave, up to 5 carried over.")
        span1 = "25 days annual leave"
        span2 = "5 carried over"
        pos1 = el.text.index(span1)
        pos2 = el.text.index(span2)
        art = _artifact(
            {"annual_days": 25, "carry_over": 5, "parental_weeks": 26},
            {
                "annual_days": DocumentRef(element_id="e1", char_start=pos1, char_end=pos1 + len(span1), verbatim_span=span1),
                "carry_over": DocumentRef(element_id="e1", char_start=pos2, char_end=pos2 + len(span2), verbatim_span=span2),
                # parental_weeks has a value but no citation → should be nulled
            },
        )
        result, failed = verify_citations(art, [el])
        assert failed == ["parental_weeks"]
        assert result.payload["annual_days"] == 25
        assert result.payload["carry_over"] == 5
        assert result.payload["parental_weeks"] is None

    def test_span_in_text_but_outside_declared_range_nulls_field(self):
        """F003: verbatim_span must be within text[char_start:char_end], not anywhere."""
        el = _element("e1", "Employees receive 25 days of annual leave.")
        # The span "25 days" is at position [17, 24] but we claim it's at [0, 7] ("Employe")
        art = _artifact(
            {"annual_days": 25},
            {"annual_days": DocumentRef(element_id="e1", char_start=0, char_end=7, verbatim_span="25 days")},
        )
        result, failed = verify_citations(art, [el])
        assert "annual_days" in failed
        assert result.payload["annual_days"] is None

    def test_span_straddles_declared_range_nulls_field(self):
        """F003: verbatim_span starting before or ending after the declared range fails."""
        el = _element("e1", "Employees receive 25 days of annual leave.")
        span = "25 days"
        pos = el.text.index(span)  # 17
        # char_start is right, but char_end is too short to include the full span
        art = _artifact(
            {"annual_days": 25},
            {"annual_days": DocumentRef(element_id="e1", char_start=pos, char_end=pos + 3, verbatim_span=span)},
        )
        result, failed = verify_citations(art, [el])
        assert "annual_days" in failed
        assert result.payload["annual_days"] is None

    def test_char_start_exceeds_text_length_nulls_field(self):
        """F003: out-of-range offsets should fail — slicing past end returns '', span won't match."""
        el = _element("e1", "Short.")
        art = _artifact(
            {"value": 1},
            {"value": DocumentRef(element_id="e1", char_start=100, char_end=110, verbatim_span="Short.")},
        )
        result, failed = verify_citations(art, [el])
        assert "value" in failed
        assert result.payload["value"] is None


class TestTabularRefVerification:
    def test_valid_tabular_ref(self):
        el = _element("row1", "25")
        art = _artifact(
            {"annual_days": 25},
            {
                "annual_days": TabularRef(
                    element_id="row1",
                    dataset="hr",
                    table="leave_policy",
                    row_id="r1",
                    column_name="annual_days",
                    verbatim_value="25",
                )
            },
        )
        result, failed = verify_citations(art, [el])
        assert failed == []

    def test_tabular_value_not_in_text_fails(self):
        el = _element("row1", "30")
        art = _artifact(
            {"annual_days": 25},
            {
                "annual_days": TabularRef(
                    element_id="row1",
                    dataset="hr",
                    table="leave_policy",
                    row_id="r1",
                    column_name="annual_days",
                    verbatim_value="25",
                )
            },
        )
        result, failed = verify_citations(art, [el])
        assert "annual_days" in failed


class TestGraphRefVerification:
    def test_valid_graph_ref(self):
        el = _element("node1", "market_cap: 1000")
        art = _artifact(
            {"market_cap": 1000},
            {
                "market_cap": GraphRef(
                    element_id="node1",
                    graph_id="kg_acme",
                    node_id="entity:acme",
                    property_name="market_cap",
                )
            },
        )
        result, failed = verify_citations(art, [el])
        assert failed == []

    def test_graph_ref_element_missing(self):
        art = _artifact(
            {"market_cap": 1000},
            {
                "market_cap": GraphRef(
                    element_id="ghost",
                    graph_id="kg",
                    node_id="n1",
                )
            },
        )
        result, failed = verify_citations(art, [])
        assert "market_cap" in failed
