<!-- gitnexus:start -->
# GitNexus — Code Intelligence

This project is indexed by GitNexus as **ake** (2093 symbols, 3744 relationships, 84 execution flows). Use the GitNexus MCP tools to understand code, assess impact, and navigate safely.

> If any GitNexus tool warns the index is stale, run `npx gitnexus analyze` in terminal first.

## Always Do

- **MUST run impact analysis before editing any symbol.** Before modifying a function, class, or method, run `gitnexus_impact({target: "symbolName", direction: "upstream"})` and report the blast radius (direct callers, affected processes, risk level) to the user.
- **MUST run `gitnexus_detect_changes()` before committing** to verify your changes only affect expected symbols and execution flows.
- **MUST warn the user** if impact analysis returns HIGH or CRITICAL risk before proceeding with edits.
- When exploring unfamiliar code, use `gitnexus_query({query: "concept"})` to find execution flows instead of grepping. It returns process-grouped results ranked by relevance.
- When you need full context on a specific symbol — callers, callees, which execution flows it participates in — use `gitnexus_context({name: "symbolName"})`.

## Never Do

- NEVER edit a function, class, or method without first running `gitnexus_impact` on it.
- NEVER ignore HIGH or CRITICAL risk warnings from impact analysis.
- NEVER rename symbols with find-and-replace — use `gitnexus_rename` which understands the call graph.
- NEVER commit changes without running `gitnexus_detect_changes()` to check affected scope.

## Resources

| Resource | Use for |
|----------|---------|
| `gitnexus://repo/ake/context` | Codebase overview, check index freshness |
| `gitnexus://repo/ake/clusters` | All functional areas |
| `gitnexus://repo/ake/processes` | All execution flows |
| `gitnexus://repo/ake/process/{name}` | Step-by-step execution trace |

## CLI

| Task | Read this skill file |
|------|---------------------|
| Understand architecture / "How does X work?" | `.claude/skills/gitnexus/gitnexus-exploring/SKILL.md` |
| Blast radius / "What breaks if I change X?" | `.claude/skills/gitnexus/gitnexus-impact-analysis/SKILL.md` |
| Trace bugs / "Why is X failing?" | `.claude/skills/gitnexus/gitnexus-debugging/SKILL.md` |
| Rename / extract / split / refactor | `.claude/skills/gitnexus/gitnexus-refactoring/SKILL.md` |
| Tools, resources, schema reference | `.claude/skills/gitnexus/gitnexus-guide/SKILL.md` |
| Index, status, clean, wiki CLI commands | `.claude/skills/gitnexus/gitnexus-cli/SKILL.md` |

<!-- gitnexus:end -->

---

# Amorphous Knowledge Engine (AKE) — Agent Guidance

## Project at a Glance

AKE is a domain-agnostic, pre-compiled artifact retrieval system. Raw sources (documents, Parquet/CSV, knowledge graphs) are ingested once; typed, cited `Artifact` records are stored in Postgres and served to agents via a declarative query interface and an MCP server layer.

The four layers in build order: **Ingestion → Artifact Compilation → Declarative Query → Compiler Loop**. Each layer has explicit handoff criteria before the next layer starts; see `docs/knowledge-engine-dev-guide.md`.

## Critical Invariants — Never Violate

- **Every non-null artifact field must have a verified citation.** Run `verify_citations()` before any write to the artifact store. Failing fields are nulled, not persisted. (ADR-002)
- **Compile at ingest, not at query time.** LLM extraction belongs in `compiler/`, never in `query/`. (ADR-001)
- **Nulls represent "not disclosed".** Never substitute `0`, `""`, or placeholder strings for a missing value. (ADR-004)
- **Filter fields are promoted columns.** `entity_id`, `artifact_type`, `fiscal_year`, `acl_principals` are real Postgres columns — never filter on JSONB paths. (ADR-005)
- **ACL enforcement lives in the database.** Postgres RLS enforces access control; application code must set `app.current_principals` on the session but must not re-implement the check. (F005)

## Key Conventions

- `doc_id` — stable content hash of the source; re-ingesting an unchanged document must produce the same `doc_id`
- `artifact_id` — deterministic hash of `(doc_id, entity_id, artifact_type)`; re-compilation must be idempotent
- `Citation` is polymorphic (`source_type: "document" | "tabular" | "graph"`) — check the discriminator before accessing `char_start`/`char_end` (ADR-008)
- Structured sources (tabular, graph) use **direct column/property mapping**; LLM calls are limited to entity resolution, ambiguous normalisation, and summary embedding only (ADR-009)
- The `execute()` function in `query/interface.py` is the only public query surface — never call the planner, fetcher, or composer directly from outside `query/`

## Where Things Live

| Concern | Location |
|---|---|
| Document / tabular / graph parsers | `ingestion/parsers/` |
| Element normalisation | `ingestion/normalizer.py` |
| Extraction prompts | `compiler/prompts/` |
| Reusable extraction skills | `compiler/skills/` |
| Citation verification | `compiler/artifact_compiler.py` |
| Postgres schema + RLS | `store/schema.sql` |
| Query planner / fetcher / composer | `query/` |
| MCP server resources and tools | `mcp/` |
| Eval sets (JSONL) | `evals/sets/` |
| Feature statements | `docs/features/` |
| Architecture decisions | `docs/adr/` |

## MCP

AKE exposes an MCP server. Resources are addressed with polymorphic URIs (`ake://artifacts/{type}/{entity_id}`, `ake://schema/{type}`, `ake://domains`). Tools map to the declarative query interface. When adding new artifact types, register the schema and URI pattern in `mcp/registry.py` so the MCP layer exposes it automatically. (F011, ADR-010)
