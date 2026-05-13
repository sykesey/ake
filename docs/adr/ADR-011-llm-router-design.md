# ADR-011 — LLM Router: LiteLLM for Provider Translation, AKE-Owned Tool Dispatch

**Status:** Accepted  
**Date:** 2026-05-13

## Context

Multiple components across all four layers make LLM calls with different requirements:

| Caller | Role | Tool calling? | Streaming? | Temperature |
|---|---|---|---|---|
| `compiler/artifact_compiler.py` | Extraction prompt | No | No | 0 (deterministic) |
| `query/composer.py` | Reshape artifacts → shape | No | Optional | 0 |
| `compiler/compiler_loop.py` → refine | Schema + code iteration | Yes (skill library) | No | 0.3–0.7 |
| `compiler/compiler_loop.py` → grader | LLM-as-judge | No | No | 0 |
| `mcp/` | Agent-initiated calls | Yes (all MCP tools) | Yes | caller-set |

Each caller needs to target potentially different models (a cheap, fast model for grading; a capable model for extraction; a powerful model for the compiler loop). Providers have different API surfaces: Anthropic uses `input_schema` in tool definitions; OpenAI uses `parameters`; Azure adds deployment-name routing; OpenAI-compatible endpoints vary in tool calling support.

Two options were considered for provider-level routing:

1. **Custom thin wrapper** — write provider-specific branches in AKE code. Simple initially, but requires AKE to absorb every API difference, retry policy, and rate-limit behaviour for each provider as the list grows.

2. **LiteLLM** — an MIT-licensed library that normalises 100+ LLM providers to a single OpenAI-compatible interface, handles retries and fallback chains, and is widely maintained. The risk is that it is a non-trivial dependency with its own abstractions.

Tool calling requires a layer that LiteLLM does not fully own: dispatching tool call responses to Python handlers or proxied MCP tools, and looping back to the provider with results. This is application logic, not provider translation.

## Decision

Use **LiteLLM as the provider translation layer only**. AKE's `LLMRouter` owns:
- The `ToolRegistry` and `ToolDefinition` schema
- Translation from AKE tool definitions to provider-specific tool schemas (via LiteLLM's format utilities)
- Detection of tool call responses
- Dispatch to registered Python handlers or MCP proxies via `MCPBridge`
- The agentic loop (feed results back, re-call provider, loop until text response)
- Retry orchestration with `tenacity` (LiteLLM's own retry is bypassed in favour of AKE-controlled backoff so we have consistent logging and fallback chain semantics)

LiteLLM is called as a stateless translation function, not as a session or client object. AKE code never imports provider SDKs (anthropic, openai) directly.

MCP tools are registered in `ToolRegistry` with `source="mcp:{server_name}"` and a proxy handler provided by `MCPBridge`. From the router's perspective, internal tools and MCP tools are identical — the dispatch path is the same.

## Consequences

**Positive**
- Adding a new LLM provider (or a new OpenAI-compatible endpoint) requires only a config change — no AKE code changes
- Provider API surface differences (tool schema format, message roles, streaming SSE format) are absorbed by LiteLLM and are not spread throughout AKE callsites
- Internal tools and MCP tools participate in the same tool-calling loop; the compiler loop can call `ake_query` as a tool without any special-casing
- The agentic loop (iterate until text response) is implemented once in `LLMRouter.complete()` and tested once, rather than being reimplemented per-caller
- Fallback chain and retry logic are centralised and consistently logged; a rate-limit event on the primary model is visible in traces regardless of which component triggered the call

**Negative**
- LiteLLM is a large dependency (~50 transitive deps); it pulls in provider SDKs and HTTP clients that AKE does not use directly. This increases the image size and the attack surface for supply-chain vulnerabilities.
- LiteLLM's own abstractions can conflict with AKE's tool calling loop in edge cases (e.g. streaming + tool calls simultaneously). These edge cases require workarounds in `LLMRouter` rather than upstream fixes.
- When LiteLLM releases a breaking change, AKE's `LLMRouter` is the adapter boundary that must be updated — this is an ongoing maintenance obligation.

**Mitigations**
- LiteLLM is pinned to a specific minor version in `uv.lock`; upgrades are deliberate and tested, not automatic.
- `LLMRouter` is covered by integration tests that mock the LiteLLM call boundary (not the provider API), so internal tool dispatch and loop logic are testable without live provider credentials.
- If LiteLLM's scope becomes a problem (size, dependency conflicts), the adapter boundary makes it replaceable with a thinner custom provider map without touching any caller code.

## Alternatives Considered

**Direct provider SDKs (anthropic + openai packages)**  
Simpler initial dependency surface but requires AKE to implement provider-specific branching for every API difference. Adding provider 3 means another branch everywhere. Rejected for maintainability at scale.

**OpenAI SDK only (rely on compatibility layers)**  
Many providers expose an OpenAI-compatible REST endpoint. Using only the openai SDK covers most cases. Fails for Anthropic-specific features (extended thinking, citation mode) that have no OpenAI equivalent, and for providers that are OpenAI-compatible but require non-standard auth flows. Rejected as too limiting for AKE's capability needs.

**Instructor / Outlines for structured output**  
Useful for extraction but are output-format libraries, not provider routers. Orthogonal to this decision; may be added inside specific extraction prompts without affecting the router interface.
