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

## What This Project Does

AKE pre-compiles structured, cited knowledge from heterogeneous sources (documents, tables, knowledge graphs) into a queryable artifact store. Agents retrieve knowledge through a declarative interface or the MCP server — they never parse raw documents or call extraction LLMs at query time.

## Hard Rules

- Do not write extraction logic inside `query/`. Extraction belongs in `compiler/`.
- Do not persist an artifact field whose citation fails `verify_citations()`. Null the field instead.
- Do not filter on JSONB paths inside `payload`. Use the promoted columns: `entity_id`, `artifact_type`, `fiscal_year`, `acl_principals`.
- Do not bypass Postgres RLS. Set `app.current_principals` on the session; do not add application-layer permission checks that duplicate or skip the RLS policy.
- Do not fill missing values with `0`, `""`, or any placeholder. Absent fields are `null`.

## Citation Model

`Citation` is a tagged union — always check `source_type` before reading source-specific fields:
- `"document"` → `element_id`, `char_start`, `char_end`, `verbatim_span`
- `"tabular"` → `element_id`, `dataset`, `table`, `row_id`, `column_name`, `verbatim_value`
- `"graph"` → `element_id`, `graph_id`, `node_id | edge_id`, `property_name`

## Structured Source Compilation

Parquet/CSV/graph sources use direct column/property mapping — no extraction prompt. LLM calls are permitted only for: entity resolution, ambiguous unit normalisation, summary text for vector embedding, and mixed-type column coercion. Do not add LLM calls to the tabular or graph compilation path without an explicit justification in the domain config.

## MCP Interface

The MCP server in `mcp/` auto-registers any artifact type present in `mcp/registry.py`. Resource URIs follow the pattern `ake://artifacts/{artifact_type}/{entity_id}`. When adding a new domain, register its schema in the MCP registry so callers discover it without code changes.

## Key Documents

- `docs/knowledge-engine-dev-guide.md` — schemas, prompts, test contracts, build order
- `docs/features/` — feature statements with acceptance criteria
- `docs/adr/` — architecture decisions and their rationale
