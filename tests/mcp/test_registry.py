"""Unit tests for the MCP schema registry (F011)."""
from __future__ import annotations

import pytest

from ake.mcp.registry import (
    ARTIFACT_TYPE_REGISTRY,
    DOMAIN_REGISTRY,
    ArtifactTypeRegistration,
    DomainRegistration,
    get_registration,
    list_domains,
    list_registrations,
    register,
)


class TestRegister:
    def test_register_creates_entry(self):
        """A new registration must appear in the registry immediately."""
        reg = register(
            artifact_type="test_type",
            domain="test_domain",
            description="A test artifact type",
            json_schema={"type": "object", "properties": {}},
            source_types=["document"],
            nullable_fields=[],
            example={},
        )
        assert reg.artifact_type == "test_type"
        assert reg.domain == "test_domain"
        assert reg in ARTIFACT_TYPE_REGISTRY.values()

    def test_register_auto_registers_domain(self):
        """Registering a type must also create a DomainRegistration if domain is new."""
        register(
            artifact_type="auto_domain_type",
            domain="auto_domain",
            description="Auto domain test",
            json_schema={"type": "object", "properties": {}},
        )
        assert "auto_domain" in DOMAIN_REGISTRY
        assert DOMAIN_REGISTRY["auto_domain"].name == "auto_domain"

    def test_register_adds_to_existing_domain(self):
        """Registering a second type in the same domain must append to artifact_types."""
        register(
            artifact_type="existing_type_a",
            domain="existing_domain",
            description="Type A",
            json_schema={"type": "object", "properties": {}},
        )
        register(
            artifact_type="existing_type_b",
            domain="existing_domain",
            description="Type B",
            json_schema={"type": "object", "properties": {}},
        )
        assert "existing_type_a" in DOMAIN_REGISTRY["existing_domain"].artifact_types
        assert "existing_type_b" in DOMAIN_REGISTRY["existing_domain"].artifact_types

    def test_default_source_types_is_document(self):
        """If source_types not specified, default to ['document']."""
        reg = register(
            artifact_type="default_source_type",
            domain="default",
            description="Test",
            json_schema={"type": "object", "properties": {}},
        )
        assert reg.source_types == ["document"]

    def test_default_promoted_filters(self):
        """Default promoted filters include entity_id, artifact_type, fiscal_year."""
        reg = register(
            artifact_type="default_filters_type",
            domain="default",
            description="Test",
            json_schema={"type": "object", "properties": {}},
        )
        assert "entity_id" in reg.promoted_filters
        assert "artifact_type" in reg.promoted_filters
        assert "fiscal_year" in reg.promoted_filters


class TestGetRegistration:
    def test_returns_registration_for_existing_type(self):
        assert get_registration("financials_10k") is not None

    def test_returns_none_for_missing_type(self):
        assert get_registration("nonexistent_type") is None


class TestListRegistrations:
    def test_lists_all_registrations(self):
        """Must include at least the financials_10k example."""
        regs = list_registrations()
        artifact_types = {r.artifact_type for r in regs}
        assert "financials_10k" in artifact_types

    def test_filters_by_domain(self):
        """Filtering by domain must only return registrations for that domain."""
        register(
            artifact_type="filter_domain_type",
            domain="filter_domain",
            description="Filter test",
            json_schema={"type": "object", "properties": {}},
        )
        regs = list_registrations(domain="filter_domain")
        for r in regs:
            assert r.domain == "filter_domain"

    def test_unknown_domain_returns_empty(self):
        regs = list_registrations(domain="nonexistent_domain")
        assert regs == []


class TestListDomains:
    def test_returns_domain_registrations(self):
        domains = list_domains()
        names = {d.name for d in domains}
        assert "financials" in names

    def test_domain_has_artifact_types(self):
        domains = list_domains()
        financials = next(d for d in domains if d.name == "financials")
        assert "financials_10k" in financials.artifact_types


class TestArtifactTypeRegistrationFields:
    def test_all_fields_present(self):
        """Verify the full Registration dataclass has all required fields."""
        reg = ArtifactTypeRegistration(
            artifact_type="test",
            domain="test",
            description="Test",
            json_schema={"type": "object"},
            source_types=["document", "tabular"],
            promoted_filters=["entity_id"],
            nullable_fields=["optional_field"],
            example={"key": "value"},
        )
        assert reg.artifact_type == "test"
        assert reg.domain == "test"
        assert reg.description == "Test"
        assert reg.json_schema == {"type": "object"}
        assert reg.source_types == ["document", "tabular"]
        assert reg.promoted_filters == ["entity_id"]
        assert reg.nullable_fields == ["optional_field"]
        assert reg.example == {"key": "value"}


class TestDomainRegistrationFields:
    def test_default_eval_status_is_none(self):
        dom = DomainRegistration(name="test", description="T", artifact_types=["t"])
        assert dom.eval_status == "none"