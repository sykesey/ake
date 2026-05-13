"""Composer — reshapes fetched artifacts into ``query.shape`` via a small LLM call.

The composer does NOT infer or estimate. If a value is not present in the
provided artifacts, it sets the field to null. Citations are threaded through
from artifact field_citations into the result's flat citation list.
"""
from __future__ import annotations

import json
import time
from typing import Any

import litellm
import structlog

from ake.compiler.artifact import DomainArtifact
from ake.compiler.citation import Citation as ArtifactCitation
from ake.config import Settings
from ake.config import settings as _default_settings
from ake.query.interface import Citation, Query, QueryResult

logger = structlog.get_logger()

_COMPOSER_SYSTEM = """You are a response composer. You reshape pre-retrieved knowledge artifacts into a structured response.

Rules:
- Populate every field in the required response shape from the provided artifacts.
- Do NOT infer, estimate, or invent values. If a field's value is not present in the artifacts, set it to null.
- Return ONLY valid JSON conforming to the required shape. No preamble, no commentary.
- If the question asks for a single value (scalar), return it directly — don't wrap it in extra layers."""


async def compose(
    query: Query,
    artifacts: list[DomainArtifact],
    settings: Settings = _default_settings,
) -> QueryResult:
    """Compose a shape-conformant response from fetched artifacts.

    Args:
        query: The original declarative query.
        artifacts: Fetched (ACL-filtered) DomainArtifacts.
        settings: Application settings (LLM provider, model, etc.)

    Returns:
        A QueryResult with data conforming to query.shape and threaded citations.
    """
    t0 = time.monotonic()
    token_cost = 0

    if not artifacts:
        return QueryResult(
            data=_null_shape(query.shape),
            citations=[],
            artifacts_used=[],
            latency_ms=int((time.monotonic() - t0) * 1000),
            token_cost=0,
        )

    artifacts_json = _artifacts_to_composer_json(artifacts)
    shape_json = json.dumps(query.shape, indent=2)

    prompt = f"""Question: {query.ask}

Required response shape:
{shape_json}

Artifacts (each with payload and citations):
{artifacts_json}

Return only valid JSON matching the required shape."""

    try:
        model = _model_string(settings)
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=[
                {"role": "system", "content": _COMPOSER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            max_tokens=4096,
            timeout=settings.llm_timeout_seconds,
        )
        if settings.llm_api_key:
            kwargs["api_key"] = settings.llm_api_key
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url

        response = await litellm.acompletion(**kwargs)  # type: ignore[call-overload]
        usage = getattr(response, "usage", None)
        if usage:
            token_cost = getattr(usage, "prompt_tokens", 0) + getattr(
                usage, "completion_tokens", 0
            )

        choices: list[Any] = getattr(response, "choices", [])
        content = getattr(choices[0].message, "content", "") if choices else ""
        data = _parse_composer_output(content, query.shape)
    except Exception:
        logger.exception("composer_llm_failed")
        # On LLM failure, return null-shaped data with artifacts_used still populated.
        data = _null_shape(query.shape)
        token_cost = 0

    # Thread citations: only for fields that are actually populated in data.
    citations: list[Citation] = []
    if query.ground:
        citations = _thread_citations(data, artifacts)

    latency_ms = int((time.monotonic() - t0) * 1000)

    return QueryResult(
        data=data,
        citations=citations,
        artifacts_used=[a.artifact_id for a in artifacts],
        latency_ms=latency_ms,
        token_cost=token_cost,
    )


# ── helpers ──────────────────────────────────────────────────────────────────


def _model_string(settings: Settings) -> str:
    m = settings.llm_model
    if "/" in m:
        return m
    provider = settings.llm_provider
    if provider in ("anthropic", "openai", "azure", "ollama"):
        return f"{provider}/{m}" if provider != "azure" else m
    return m


def _artifacts_to_composer_json(artifacts: list[DomainArtifact]) -> str:
    """Serialize artifacts for the composer prompt, attaching citation info."""
    out: list[dict[str, Any]] = []
    for a in artifacts:
        field_cites = {
            field: {
                "element_id": cite.element_id,
                "verbatim_span": _verbatim_from_citation(cite),
                "source_type": cite.source_type,
            }
            for field, cite in a.field_citations.items()
        }
        out.append(
            {
                "artifact_id": a.artifact_id,
                "doc_id": a.doc_id,
                "entity_id": a.entity_id,
                "artifact_type": a.artifact_type,
                "fiscal_year": a.fiscal_year,
                "payload": a.payload,
                "field_citations": field_cites,
            }
        )
    return json.dumps(out, indent=2)


def _verbatim_from_citation(cite: ArtifactCitation) -> str:
    """Extract verbatim text from a polymorphic Citation."""
    if cite.source_type == "document":
        return cite.verbatim_span
    elif cite.source_type == "tabular":
        return cite.verbatim_value
    elif cite.source_type == "graph":
        return cite.property_name or ""
    return ""


def _null_shape(shape: dict[str, Any]) -> dict[str, Any]:
    """Produce a null-filled dict matching the shape structure."""
    result: dict[str, Any] = {}
    for key, val in shape.items():
        if isinstance(val, dict):
            result[key] = _null_shape(val)
        elif isinstance(val, list):
            result[key] = []
        else:
            result[key] = None
    return result


def _parse_composer_output(content: str, shape: dict[str, Any]) -> dict[str, Any]:
    """Parse LLM output into a dict, falling back to null shape on failure."""
    content = content.strip()
    # Strip markdown code fences if present.
    if content.startswith("```"):
        lines = content.splitlines()
        if lines[0].strip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        content = "\n".join(lines)

    try:
        return json.loads(content)
    except json.JSONDecodeError:
        logger.warning("composer_json_parse_failed", content_preview=content[:200])
        return _null_shape(shape)


def _thread_citations(
    data: dict[str, Any],
    artifacts: list[DomainArtifact],
) -> list[Citation]:
    """Build a flat citation list for all populated fields in data.

    Matches field names in ``data`` against artifact field_citations keys.
    For nested shapes, recurse. For list values, iterate.
    """
    citations: list[Citation] = []
    _collect_citations(data, artifacts, citations, prefix="")
    return citations


def _collect_citations(
    node: Any,
    artifacts: list[DomainArtifact],
    citations: list[Citation],
    prefix: str,
) -> None:
    if node is None:
        return

    if isinstance(node, dict):
        for key, val in node.items():
            field_path = f"{prefix}.{key}" if prefix else key
            if val is not None:
                _collect_citations(val, artifacts, citations, field_path)
            # Look up citation for this field in artifact field_citations.
            for a in artifacts:
                cite = a.field_citations.get(field_path)
                if cite is None:
                    # Try matching just the leaf key.
                    cite = a.field_citations.get(key)
                if cite is not None:
                    citations.append(
                        Citation(
                            field=field_path,
                            element_id=cite.element_id,
                            verbatim_span=_verbatim_from_citation(cite),
                            doc_id=a.doc_id,
                        )
                    )

    elif isinstance(node, list):
        for idx, item in enumerate(node):
            field_path = f"{prefix}[{idx}]"
            _collect_citations(item, artifacts, citations, field_path)