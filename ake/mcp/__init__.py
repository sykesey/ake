"""MCP server layer — polymorphic resources, tools, and schema registry (F011).

Exposes the full AKE capability surface as a standards-compliant MCP server.
Importing this module auto-registers all domains in the schema registry.
"""

from __future__ import annotations

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
from ake.mcp.server import mcp, run_sse, run_stdio

__all__ = [
    "mcp",
    "run_stdio",
    "run_sse",
    "register",
    "get_registration",
    "list_registrations",
    "list_domains",
    "ARTIFACT_TYPE_REGISTRY",
    "DOMAIN_REGISTRY",
    "ArtifactTypeRegistration",
    "DomainRegistration",
]