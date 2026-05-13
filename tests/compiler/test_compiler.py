"""Tests for ArtifactCompiler — LLM calls are mocked."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ake.compiler.artifact import DomainSchema, FieldSpec, compute_artifact_id
from ake.compiler.artifact_compiler import ArtifactCompiler, ExtractionError
from ake.compiler.citation import DocumentRef
from ake.ingestion.element import Element
from ake.llm.router import LLMResponse


# ── Helpers ───────────────────────────────────────────────────────────────────


def _element(element_id: str, text: str, doc_id: str = "doc1") -> Element:
    return Element(
        doc_id=doc_id,
        element_id=element_id,
        type="paragraph",
        text=text,
        page=1,
        section_path=["Leave Policy"],
        metadata={"acl_principals": ["group:all-employees"]},
    )


def _hr_schema() -> DomainSchema:
    return DomainSchema(
        artifact_type="leave_policy",
        description="HR leave entitlement policy for a department",
        entity_id_field="department",
        fields={
            "department": FieldSpec(type="str", description="Department name", required=True),
            "annual_days": FieldSpec(type="int", description="Annual leave days"),
            "carry_over_days": FieldSpec(type="int", description="Max carry-over days"),
        },
    )


def _mock_router(response_content: str) -> MagicMock:
    router = MagicMock()
    router.complete = AsyncMock(
        return_value=LLMResponse(
            content=response_content,
            tool_calls_made=[],
            input_tokens=100,
            output_tokens=50,
            model_used="claude-sonnet-4-6",
            provider_used="anthropic",
        )
    )
    return router


def _good_response(entity_id: str, annual_days: int, element_id: str, verbatim: str) -> str:
    return json.dumps({
        "entity_id": entity_id,
        "fiscal_year": None,
        "fields": {
            "department": {
                "value": entity_id,
                "source": {"element_id": element_id, "verbatim_span": verbatim},
            },
            "annual_days": {
                "value": annual_days,
                "source": {"element_id": element_id, "verbatim_span": str(annual_days)},
            },
            "carry_over_days": {"value": None},
        },
    })


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestArtifactCompilerHappyPath:
    @pytest.mark.asyncio
    async def test_produces_artifact_with_correct_ids(self):
        el = _element("e1", "HR department receives 25 days annual leave.")
        response = _good_response("HR", 25, "e1", "25")
        compiler = ArtifactCompiler(_mock_router(response))

        artifact, failed = await compiler.compile([el], _hr_schema())

        expected_id = compute_artifact_id("doc1", "HR", "leave_policy")
        assert artifact.artifact_id == expected_id
        assert artifact.entity_id == "HR"
        assert artifact.artifact_type == "leave_policy"
        assert artifact.doc_id == "doc1"

    @pytest.mark.asyncio
    async def test_verified_fields_have_citations(self):
        el = _element("e1", "HR receives 25 days annual leave.")
        response = _good_response("HR", 25, "e1", "25")
        compiler = ArtifactCompiler(_mock_router(response))

        artifact, failed = await compiler.compile([el], _hr_schema())

        assert "annual_days" in artifact.field_citations
        citation = artifact.field_citations["annual_days"]
        assert isinstance(citation, DocumentRef)
        assert citation.verbatim_span == "25"
        assert citation.element_id == "e1"

    @pytest.mark.asyncio
    async def test_null_field_has_no_citation(self):
        el = _element("e1", "HR receives 25 days annual leave.")
        response = _good_response("HR", 25, "e1", "25")
        compiler = ArtifactCompiler(_mock_router(response))

        artifact, _ = await compiler.compile([el], _hr_schema())

        assert artifact.payload["carry_over_days"] is None
        assert "carry_over_days" not in artifact.field_citations

    @pytest.mark.asyncio
    async def test_acl_principals_propagated_from_elements(self):
        el = _element("e1", "HR receives 25 days annual leave.")
        response = _good_response("HR", 25, "e1", "25")
        compiler = ArtifactCompiler(_mock_router(response))

        artifact, _ = await compiler.compile([el], _hr_schema())

        assert artifact.acl_principals == ["group:all-employees"]

    @pytest.mark.asyncio
    async def test_idempotent_artifact_id(self):
        el = _element("e1", "HR receives 25 days annual leave.")
        response = _good_response("HR", 25, "e1", "25")
        compiler = ArtifactCompiler(_mock_router(response))

        a1, _ = await compiler.compile([el], _hr_schema())
        a2, _ = await compiler.compile([el], _hr_schema())
        assert a1.artifact_id == a2.artifact_id


class TestCitationVerificationIntegration:
    @pytest.mark.asyncio
    async def test_bad_verbatim_span_nulls_field(self):
        el = _element("e1", "HR receives 25 days annual leave.")
        # LLM returns a span that isn't in the element text
        response = json.dumps({
            "entity_id": "HR",
            "fiscal_year": None,
            "fields": {
                "department": {"value": "HR", "source": {"element_id": "e1", "verbatim_span": "HR"}},
                "annual_days": {
                    "value": 30,
                    "source": {"element_id": "e1", "verbatim_span": "30 days"},  # not present
                },
                "carry_over_days": {"value": None},
            },
        })
        compiler = ArtifactCompiler(_mock_router(response))

        artifact, failed = await compiler.compile([el], _hr_schema())

        assert "annual_days" in failed
        assert artifact.payload["annual_days"] is None

    @pytest.mark.asyncio
    async def test_wrong_element_id_nulls_field(self):
        el = _element("e1", "25 days leave.")
        response = json.dumps({
            "entity_id": "HR",
            "fiscal_year": None,
            "fields": {
                "department": {"value": "HR", "source": {"element_id": "e1", "verbatim_span": "HR"}},
                "annual_days": {
                    "value": 25,
                    "source": {"element_id": "ghost_element", "verbatim_span": "25"},
                },
                "carry_over_days": {"value": None},
            },
        })
        compiler = ArtifactCompiler(_mock_router(response))

        artifact, failed = await compiler.compile([el], _hr_schema())

        assert "annual_days" in failed
        assert artifact.payload["annual_days"] is None


class TestExtractionErrors:
    @pytest.mark.asyncio
    async def test_non_json_response_raises(self):
        el = _element("e1", "Some text.")
        compiler = ArtifactCompiler(_mock_router("This is not JSON at all."))

        with pytest.raises(ExtractionError, match="non-JSON"):
            await compiler.compile([el], _hr_schema())

    @pytest.mark.asyncio
    async def test_missing_entity_id_raises(self):
        el = _element("e1", "Some text.")
        response = json.dumps({"fiscal_year": None, "fields": {}})
        compiler = ArtifactCompiler(_mock_router(response))

        with pytest.raises(ExtractionError, match="entity_id"):
            await compiler.compile([el], _hr_schema())

    @pytest.mark.asyncio
    async def test_empty_elements_raises(self):
        compiler = ArtifactCompiler(_mock_router("{}"))
        with pytest.raises(ValueError, match="non-empty"):
            await compiler.compile([], _hr_schema())

    @pytest.mark.asyncio
    async def test_markdown_fence_stripped(self):
        el = _element("e1", "HR receives 25 days annual leave.")
        inner = _good_response("HR", 25, "e1", "25")
        fenced = f"```json\n{inner}\n```"
        compiler = ArtifactCompiler(_mock_router(fenced))

        artifact, _ = await compiler.compile([el], _hr_schema())
        assert artifact.entity_id == "HR"


class TestFiscalYear:
    @pytest.mark.asyncio
    async def test_fiscal_year_extracted(self):
        schema = DomainSchema(
            artifact_type="annual_report",
            description="Annual financial summary",
            entity_id_field="company",
            fields={"company": FieldSpec(type="str", description="Company name")},
            fiscal_year_field="year",
        )
        el = _element("e1", "Acme Corp 2024 annual report.")
        response = json.dumps({
            "entity_id": "Acme Corp",
            "fiscal_year": 2024,
            "fields": {
                "company": {"value": "Acme Corp", "source": {"element_id": "e1", "verbatim_span": "Acme Corp"}},
            },
        })
        compiler = ArtifactCompiler(_mock_router(response))

        artifact, _ = await compiler.compile([el], schema)
        assert artifact.fiscal_year == 2024

    @pytest.mark.asyncio
    async def test_invalid_fiscal_year_becomes_none(self):
        schema = DomainSchema(
            artifact_type="annual_report",
            description="Annual summary",
            entity_id_field="company",
            fields={"company": FieldSpec(type="str", description="Company name")},
        )
        el = _element("e1", "Acme Corp report.")
        response = json.dumps({
            "entity_id": "Acme Corp",
            "fiscal_year": "not-a-year",
            "fields": {
                "company": {"value": "Acme Corp", "source": {"element_id": "e1", "verbatim_span": "Acme Corp"}},
            },
        })
        compiler = ArtifactCompiler(_mock_router(response))

        artifact, _ = await compiler.compile([el], schema)
        assert artifact.fiscal_year is None
