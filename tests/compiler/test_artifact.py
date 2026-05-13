"""Tests for DomainArtifact and artifact_id computation."""
from __future__ import annotations

from ake.compiler.artifact import DomainArtifact, DomainSchema, FieldSpec, compute_artifact_id


class TestComputeArtifactId:
    def test_deterministic(self):
        a = compute_artifact_id("doc1", "entity1", "policy")
        b = compute_artifact_id("doc1", "entity1", "policy")
        assert a == b

    def test_different_inputs_differ(self):
        a = compute_artifact_id("doc1", "entity1", "policy")
        b = compute_artifact_id("doc1", "entity1", "handbook")
        c = compute_artifact_id("doc2", "entity1", "policy")
        assert a != b
        assert a != c
        assert b != c

    def test_returns_hex_string(self):
        result = compute_artifact_id("d", "e", "t")
        assert all(c in "0123456789abcdef" for c in result)
        assert len(result) == 64  # sha256 hex


class TestDomainSchema:
    def test_basic_construction(self):
        schema = DomainSchema(
            artifact_type="leave_policy",
            description="HR leave entitlement policy",
            entity_id_field="department",
            fields={
                "annual_days": FieldSpec(type="int", description="Annual leave days"),
                "carry_over_days": FieldSpec(type="int", description="Max carry-over days"),
            },
            fiscal_year_field=None,
        )
        assert schema.artifact_type == "leave_policy"
        assert "annual_days" in schema.fields
        assert schema.fields["annual_days"].type == "int"

    def test_required_field_flag(self):
        schema = DomainSchema(
            artifact_type="t",
            description="d",
            entity_id_field="name",
            fields={
                "req": FieldSpec(description="required field", required=True),
                "opt": FieldSpec(description="optional field", required=False),
            },
        )
        assert schema.fields["req"].required is True
        assert schema.fields["opt"].required is False


class TestDomainArtifact:
    def test_construction(self):
        artifact_id = compute_artifact_id("doc1", "hr", "leave_policy")
        art = DomainArtifact(
            artifact_id=artifact_id,
            doc_id="doc1",
            entity_id="hr",
            artifact_type="leave_policy",
            fiscal_year=None,
            payload={"annual_days": 25},
            field_citations={},
            acl_principals=["group:all-employees"],
        )
        assert art.artifact_id == artifact_id
        assert art.payload["annual_days"] == 25
        assert art.acl_principals == ["group:all-employees"]

    def test_compiled_at_is_utc(self):
        import datetime
        art = DomainArtifact(
            artifact_id="x",
            doc_id="d",
            entity_id="e",
            artifact_type="t",
            fiscal_year=None,
            payload={},
            field_citations={},
            acl_principals=[],
        )
        assert art.compiled_at.tzinfo is not None
        assert art.compiled_at.tzinfo == datetime.timezone.utc
