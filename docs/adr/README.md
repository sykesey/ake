# Architecture Decision Records

Each ADR captures a significant architectural decision: the context that forced a choice, the decision made, and the trade-offs accepted.

| ID | Title | Status |
|---|---|---|
| [ADR-001](ADR-001-compile-at-ingest.md) | Compile Artifacts at Ingest, Not at Query Time | Accepted |
| [ADR-002](ADR-002-citations-mandatory.md) | Citations Are Mandatory for All Non-Null Fields | Accepted |
| [ADR-003](ADR-003-postgres-pgvector-first.md) | Start with Postgres + pgvector; Defer Polyglot Persistence | Accepted |
| [ADR-004](ADR-004-nulls-first-class.md) | Nulls Are First-Class; "Not Disclosed" Must Be Representable | Accepted |
| [ADR-005](ADR-005-promoted-columns.md) | Promote Filter Fields from JSONB to Real Columns | Accepted |
| [ADR-006](ADR-006-separation-of-concerns.md) | Structured Filtering, Semantic Search, and ACL Are Distinct Subsystems | Accepted |
| [ADR-007](ADR-007-llm-as-judge-grading.md) | Hybrid Exact-Match + LLM-as-Judge Grading for the Compiler Loop | Accepted |
| [ADR-008](ADR-008-polymorphic-citation-addressing.md) | Polymorphic Citation Addressing for Non-Document Sources | Accepted |
| [ADR-009](ADR-009-direct-mapping-vs-llm-extraction.md) | Direct Mapping for Structured Sources; LLM Extraction Only Where Needed | Accepted |
| [ADR-010](ADR-010-mcp-as-agent-interface.md) | MCP as the Primary Agent-Facing Interface | Accepted |
| [ADR-011](ADR-011-llm-router-design.md) | LLM Router: LiteLLM for Provider Translation, AKE-Owned Tool Dispatch | Accepted |
| [ADR-012](ADR-012-zero-declaration-schema-derivation.md) | Zero-Declaration Schema Derivation for Amorphous Sources | Accepted |
| [ADR-013](ADR-013-two-pass-fk-inference.md) | Two-Pass FK Inference: Naming Convention Followed by Value Overlap | Accepted |
| [ADR-014](ADR-014-owl-and-multi-format-ontology-export.md) | OWL 2 / RDF as Canonical Ontology Representation; Multi-Format Export | Accepted |

## Adding a new ADR

Copy any existing ADR as a template. Increment the number. Status options: `Proposed`, `Accepted`, `Superseded`, `Deprecated`. If superseding an existing ADR, update its status and link to the new one.
