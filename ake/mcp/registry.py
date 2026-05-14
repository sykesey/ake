"""Schema registry — single source of truth for all registered artifact types and domains.

Adding a new domain requires only registering its schema here.
The MCP server auto-discovers all types at startup via iterating this registry.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ArtifactTypeRegistration:
    """Fully describes one artifact type for discovery, schema consumption, and query construction."""

    artifact_type: str
    domain: str
    description: str
    json_schema: dict[str, Any]
    source_types: list[str]  # which source types can produce this type
    promoted_filters: list[str]  # columns safe to filter on
    nullable_fields: list[str]  # fields that may be null
    example: dict[str, Any] = field(default_factory=dict)  # representative artifact payload


@dataclass
class DomainRegistration:
    """Describes a registered domain."""

    name: str
    description: str
    artifact_types: list[str]
    eval_status: str = "none"  # none | pending | passed | failed


# The global registry — populated at import time.
# A new artifact type registered here becomes immediately discoverable via MCP
# without any server code changes (F011 acceptance criteria).
ARTIFACT_TYPE_REGISTRY: dict[str, ArtifactTypeRegistration] = {}

DOMAIN_REGISTRY: dict[str, DomainRegistration] = {}


def register(
    artifact_type: str,
    domain: str,
    description: str,
    json_schema: dict[str, Any],
    *,
    source_types: list[str] | None = None,
    promoted_filters: list[str] | None = None,
    nullable_fields: list[str] | None = None,
    example: dict[str, Any] | None = None,
) -> ArtifactTypeRegistration:
    """Register an artifact type. Returns the registration for inline use."""
    reg = ArtifactTypeRegistration(
        artifact_type=artifact_type,
        domain=domain,
        description=description,
        json_schema=json_schema,
        source_types=source_types or ["document"],
        promoted_filters=promoted_filters or ["entity_id", "artifact_type", "fiscal_year"],
        nullable_fields=nullable_fields or [],
        example=example or {},
    )
    ARTIFACT_TYPE_REGISTRY[artifact_type] = reg

    # Auto-register domain if not already present.
    if domain not in DOMAIN_REGISTRY:
        DOMAIN_REGISTRY[domain] = DomainRegistration(
            name=domain,
            description=description,
            artifact_types=[artifact_type],
        )
    else:
        dom = DOMAIN_REGISTRY[domain]
        if artifact_type not in dom.artifact_types:
            dom.artifact_types.append(artifact_type)

    return reg


def get_registration(artifact_type: str) -> ArtifactTypeRegistration | None:
    """Look up a registered artifact type."""
    return ARTIFACT_TYPE_REGISTRY.get(artifact_type)


def list_registrations(domain: str | None = None) -> list[ArtifactTypeRegistration]:
    """List all registrations, optionally filtered by domain."""
    if domain is None:
        return list(ARTIFACT_TYPE_REGISTRY.values())
    return [r for r in ARTIFACT_TYPE_REGISTRY.values() if r.domain == domain]


def list_domains() -> list[DomainRegistration]:
    """List all registered domains."""
    return list(DOMAIN_REGISTRY.values())


# ═════════════════════════════════════════════════════════════════════════════
# Example registrations (placeholder — real domains register here via import)
# ═════════════════════════════════════════════════════════════════════════════

register(
    artifact_type="financials_10k",
    domain="financials",
    description="Annual financial data from 10-K filings including revenue, net income, EPS, assets, and liabilities.",
    json_schema={
        "type": "object",
        "properties": {
            "fiscal_year": {"type": "integer", "description": "Fiscal year of the filing"},
            "total_revenue": {"type": "number", "description": "Total revenue in millions USD", "nullable": True},
            "net_income": {"type": "number", "description": "Net income in millions USD", "nullable": True},
            "diluted_eps": {"type": "number", "description": "Diluted earnings per share", "nullable": True},
            "total_assets": {"type": "number", "description": "Total assets in millions USD", "nullable": True},
            "total_liabilities": {"type": "number", "description": "Total liabilities in millions USD", "nullable": True},
            "operating_cash_flow": {"type": "number", "description": "Operating cash flow in millions USD", "nullable": True},
            "share_repurchases": {"type": "number", "description": "Share repurchase amount in millions USD", "nullable": True},
        },
        "required": ["fiscal_year"],
    },
    source_types=["document", "tabular"],
    nullable_fields=[
        "total_revenue", "net_income", "diluted_eps", "total_assets",
        "total_liabilities", "operating_cash_flow", "share_repurchases",
    ],
    example={
        "fiscal_year": 2024,
        "total_revenue": 60922,
        "net_income": 29760,
        "diluted_eps": 12.05,
        "total_assets": 65728,
        "total_liabilities": 22750,
        "operating_cash_flow": 28091,
        "share_repurchases": 6560,
    },
)