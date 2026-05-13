"""Eval-driven compiler loop — auto-tunes extraction per domain (F007).

Bootstrap → curate → evaluate → grade → refine loop.  Given a domain
description, an eval set, and a parsed corpus, the compiler autonomously
produces artifact schemas and extraction code that achieve a target accuracy
threshold (default: 0.85).
"""

from __future__ import annotations

import json
import logging
import re
import statistics
import textwrap
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ake.compiler.artifact import DomainSchema
from ake.compiler.skills import SKILL_REGISTRY
from ake.ingestion.element import Element
from ake.llm.router import LLMRequest, LLMRouter

logger = logging.getLogger(__name__)

# ═════════════════════════════════════════════════════════════════════════════
# Data types
# ═════════════════════════════════════════════════════════════════════════════


class FailureClass(str, Enum):
    """Structural failure bucketing so the refine prompt sees patterns."""

    MISSING_ARTIFACT = "missing_artifact"
    WRONG_GRANULARITY = "wrong_granularity"
    CITATION_GAP = "citation_gap"
    MULTI_ENTITY_MISS = "multi_entity_miss"
    SECTION_MISS = "section_miss"
    UNIT_ERROR = "unit_error"
    UNKNOWN = "unknown"


@dataclass
class EvalItem:
    """One eval question / ground-truth pair."""

    id: str
    question: str
    answer: dict[str, Any]
    difficulty: str  # single_fact | multi_fact | multi_company | multi_step
    entities: list[str] = field(default_factory=list)


@dataclass
class ParsedDoc:
    """Layer 1 output for one document — fed into the curator."""

    doc_id: str
    elements: list[Element]


@dataclass
class FailureCase:
    """A single eval item whose score fell below the failure threshold."""

    question_id: str
    difficulty: str
    expected: dict[str, Any]
    got: dict[str, Any]
    artifacts_used: list[str]
    failure_class: FailureClass


@dataclass
class FailureReport:
    """Structured failure analysis for the refine step."""

    failures: list[FailureCase]
    by_difficulty: dict[str, list[FailureCase]]


@dataclass
class CompiledContext:
    """The output of a compiler loop run — code strings plus score metadata."""

    schema_code: str
    curate_code: str
    query_code: str
    score: float
    scores_by_difficulty: dict[str, float]
    iterations: int


# ═════════════════════════════════════════════════════════════════════════════
# Grader
# ═════════════════════════════════════════════════════════════════════════════


def exact_match(got: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Return True when got and expected are recursively equal."""
    if type(got) is not type(expected):
        return False
    if isinstance(expected, dict):
        got_keys = set(got.keys())
        exp_keys = set(expected.keys())
        if got_keys != exp_keys:
            return False
        return all(exact_match(got[k], expected[k]) for k in expected)
    if isinstance(expected, list):
        if len(got) != len(expected):
            return False
        return all(exact_match(g, e) for g, e in zip(got, expected))
    return got == expected


async def llm_judge(
    got: dict[str, Any],
    expected: dict[str, Any],
    question: str,
    router: LLMRouter,
) -> float:
    """LLM-as-judge scoring for responses that don't exactly match expected.

    Returns a float in [0, 1] representing how close the answer is.
    """
    prompt = textwrap.dedent(f"""\
        You are grading an answer to a question.

        Question: {question}

        Expected answer:
        {json.dumps(expected, indent=2)}

        Actual answer:
        {json.dumps(got, indent=2)}

        Rate the actual answer on a scale of 0 to 1 where:
        1.0 = completely correct
        0.8 = largely correct, minor difference
        0.5 = partially correct
        0.0 = completely wrong

        Return ONLY a single float between 0 and 1. No explanation.
    """)

    request = LLMRequest(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=16,
    )

    response = await router.complete(request)
    content = response.content.strip()
    try:
        score = float(content)
        return max(0.0, min(1.0, score))
    except ValueError:
        # Try to extract a float from the response
        match = re.search(r"([\d.]+)", content)
        if match:
            return max(0.0, min(1.0, float(match.group(1))))
        return 0.0


# ═════════════════════════════════════════════════════════════════════════════
# Failure classification
# ═════════════════════════════════════════════════════════════════════════════


def classify_failure(result: dict[str, Any], item: EvalItem) -> FailureClass:
    """Classify a failed eval item into a structural failure bucket.

    Heuristics are based on result characteristics before falling back
    to UNKNOWN.  Order matters — more specific checks run first.
    """
    got = result
    expected = item.answer

    # Check for unit-like errors: numeric values that are off by powers of 10
    if _has_unit_divergence(got, expected):
        return FailureClass.UNIT_ERROR

    # Citation gap: results exist but all expected fields are null
    if _all_values_null(got, expected):
        return FailureClass.CITATION_GAP

    # Check for missing entity entirely (empty or all-null result)
    if _is_empty_or_null(got):
        return FailureClass.MISSING_ARTIFACT

    # Check for multi-entity patterns
    if item.difficulty in ("multi_company", "multi_fact", "multi_step"):
        if not _covers_all_entities(got, expected):
            return FailureClass.MULTI_ENTITY_MISS

    # Check for granularity — too many or too few fields
    if _has_wrong_granularity(got, expected):
        return FailureClass.WRONG_GRANULARITY

    # Check for section miss — result has data but wrong values
    if _has_wrong_values(got, expected):
        return FailureClass.SECTION_MISS

    return FailureClass.UNKNOWN


def _is_empty_or_null(data: dict[str, Any]) -> bool:
    """Check if the result is empty or all null."""
    if not data:
        return True
    return all(v is None for v in data.values())


def _all_values_null(got: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Check if all expected fields are null in the result.

    Returns True only when:
    - At least one expected key appears in got with a null value (the
      extraction knew about the field but couldn't ground it), AND
    - Got also has at least one non-null value for *some* field
      (showing the extraction partially works — a true citation gap,
      not a complete miss).
    """
    has_any_non_null = any(v is not None for v in got.values())
    if not has_any_non_null:
        return False  # All-null or empty — missing artifact, not citation gap

    for key in expected:
        if key in got and got[key] is not None:
            return False  # At least one expected field has a value
    return True


def _has_unit_divergence(got: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Detect values that are likely the same magnitude but wrong units.

    Checks for divergences at 10×, 100×, 1000×, 1,000,000× (and their
    reciprocals), covering common unit errors (dollars vs thousands, millions,
    billions, etc.).
    """
    for key in expected:
        ev = expected[key]
        gv = got.get(key)
        if isinstance(ev, (int, float)) and isinstance(gv, (int, float)):
            if ev != 0 and gv != 0:
                ratio = abs(gv / ev)
                # Check log-10 scale: ratio is roughly a power of 10
                # but not near 1.0 (which would be exact match territory)
                for power in (10, 100, 1_000, 1_000_000):
                    lower = power * 0.9
                    upper = power * 1.1
                    if lower < ratio < upper:
                        return True
                    # Also check the reciprocal direction
                    recip_lower = (1.0 / power) * 0.9
                    recip_upper = (1.0 / power) * 1.1
                    if recip_lower < ratio < recip_upper:
                        return True
    return False


def _covers_all_entities(got: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Check if all expected entity keys are present."""
    expected_keys = set(expected.keys())
    got_keys = set(got.keys())
    return expected_keys.issubset(got_keys)


def _has_wrong_granularity(got: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Check if the response has too many or too few keys vs expected."""
    got_keys = set(k for k, v in got.items() if v is not None)
    exp_keys = set(k for k, v in expected.items() if v is not None)
    if len(got_keys) == 0:
        return False
    ratio = len(got_keys) / max(len(exp_keys), 1)
    return ratio < 0.5 or ratio > 1.5


def _has_wrong_values(got: dict[str, Any], expected: dict[str, Any]) -> bool:
    """Check if any values in the result are wrong compared to expected."""
    for key in expected:
        if key not in got:
            return True
        if got[key] != expected[key]:
            return True
    return False


# ═════════════════════════════════════════════════════════════════════════════
# Bootstrap prompts
# ═════════════════════════════════════════════════════════════════════════════

BOOTSTRAP_SCHEMA_PROMPT = """\
You are designing an artifact extraction schema for a new domain.

Domain description: {domain_description}

Here are 5 example user questions this domain must answer:
{seed_questions}

Available skills you can reference in your code:
{skill_signatures}

Design:
1. A DomainSchema with artifact_type, description, entity_id_field, and fields
   with typed FieldSpec entries.
2. A `curate(doc: ParsedDoc) -> list[DomainArtifact]` function that extracts
   artifacts from document elements using the available skills.
3. A `query(question: str, artifacts: list[DomainArtifact]) -> dict` function
   that answers a natural-language question from compiled artifacts.

Rules:
- Use the available skills where possible; prefer composing skills over
  writing new logic.
- The curate function receives a ParsedDoc with .doc_id and .elements
  (list[Element] with .element_id, .text, .section_path, .type).
- Use find_section() to navigate document structure.
- Use extract_table() for tabular data.
- Use extract_named_entities() + normalize_currency() / normalize_date()
  for entity extraction.
- Use locate_by_proximity() to find context near anchor phrases.
- Use resolve_entity() to map entity mentions to canonical IDs.
- The query function should be deterministic — no LLM calls.
- Every non-null field MUST include a citation.

Return THREE code blocks, one for each section, using exactly these markers:

```schema
<imports and DomainSchema definition>
```

```curate
<imports and curate function>
```

```query
<imports and query function>
```
"""

REFINE_PROMPT = """\
You are improving artifact extraction code for the domain: {domain_description}

Current schema:
```python
{schema_code}
```

Current curate() function:
```python
{curate_code}
```

Current query() function:
```python
{query_code}
```

Failure report from iteration {iteration}:
{failure_report_json}

Available skills:
{skill_signatures}

Instructions:
- Analyze the failure patterns, especially by difficulty tier
- Propose minimal diffs to schema_code, curate_code, and/or query_code
- Prefer composing existing skills over writing new logic
- Do not change the Citation or field_citations contract
- Every non-null field MUST include a citation

Return THREE code blocks:
```schema
<updated schema code>
```

```curate
<updated curate code>
```

```query
<updated query code>
```
"""


def _skill_signatures_text() -> str:
    """Build a text block describing available skills."""
    parts: list[str] = []
    for name, sig in SKILL_REGISTRY.items():
        parts.append(f"{name}: {sig}")
    return "\n".join(parts)


def _seed_questions_text(items: list[EvalItem]) -> str:
    """Format 5 seed eval items as a text block."""
    parts: list[str] = []
    for item in items[:5]:
        parts.append(
            f"Q: {item.question}\nA: {json.dumps(item.answer)} "
            f"(difficulty: {item.difficulty})"
        )
    return "\n\n".join(parts)


def _failure_report_json(report: FailureReport) -> str:
    """Serialize a FailureReport to JSON for the refine prompt."""
    failure_summaries: list[dict] = []
    for f in report.failures:
        failure_summaries.append(
            {
                "question_id": f.question_id,
                "difficulty": f.difficulty,
                "failure_class": f.failure_class.value,
                "expected": f.expected,
                "got": f.got,
            }
        )

    by_difficulty: dict[str, int] = {}
    for diff, cases in report.by_difficulty.items():
        by_difficulty[diff] = len(cases)

    return json.dumps(
        {
            "total_failures": len(report.failures),
            "failures_by_difficulty": by_difficulty,
            "failure_details": failure_summaries,
        },
        indent=2,
    )


# ═════════════════════════════════════════════════════════════════════════════
# Code parsing helpers
# ═════════════════════════════════════════════════════════════════════════════


def _extract_code_block(text: str, marker: str) -> str:
    """Extract a fenced code block marked with ```<marker>."""
    pattern = rf"```{marker}\s*\n(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    # Try without the marker — just any code block
    pattern_generic = r"```(?:python)?\s*\n(.*?)```"
    matches = re.findall(pattern_generic, text, re.DOTALL)
    if matches:
        idx = {"schema": 0, "curate": 1, "query": 2}.get(marker, 0)
        idx = min(idx, len(matches) - 1)
        return matches[idx].strip()
    raise ValueError(f"Could not extract code block for marker '{marker}'")


def _parse_llm_response(response_text: str) -> tuple[str, str, str]:
    """Parse the LLM's three code blocks (schema, curate, query)."""
    schema_code = _extract_code_block(response_text, "schema")
    curate_code = _extract_code_block(response_text, "curate")
    query_code = _extract_code_block(response_text, "query")
    return schema_code, curate_code, query_code


# ═════════════════════════════════════════════════════════════════════════════
# Code execution sandbox
# ═════════════════════════════════════════════════════════════════════════════


def _exec_code(code: str, globals_dict: dict[str, Any] | None = None) -> dict[str, Any]:
    """Execute a Python code string and return the resulting module namespace.

    Returns a dict of the globals after execution so callers can retrieve
    functions defined by the code.
    """
    if globals_dict is None:
        globals_dict = {}
    exec(code, globals_dict)
    return globals_dict


def run_curate(
    curate_code: str,
    corpus: list[ParsedDoc],
) -> list[Any]:
    """Execute curate code against a parsed corpus.

    The curate code string must define a `curate(doc: ParsedDoc) -> list[DomainArtifact]`
    function. This function imports self into a namespace and invokes it for every doc.
    """
    namespace: dict[str, Any] = {}
    _exec_code(curate_code, namespace)

    curate_fn = namespace.get("curate")
    if curate_fn is None:
        raise ValueError("curate_code must define a 'curate' function")

    all_artifacts: list[Any] = []
    for doc in corpus:
        try:
            artifacts = curate_fn(doc)
            if artifacts:
                all_artifacts.extend(artifacts)
        except Exception as exc:
            logger.warning("curate_failed doc_id=%s error=%s", doc.doc_id, exc)

    return all_artifacts


def run_query(
    query_code: str,
    question: str,
    artifacts: list[Any],
) -> dict[str, Any]:
    """Execute query code against a set of artifacts.

    The query code string must define a `query(question: str, artifacts: list[
    DomainArtifact]) -> dict` function.
    """
    namespace: dict[str, Any] = {}
    _exec_code(query_code, namespace)

    query_fn = namespace.get("query")
    if query_fn is None:
        raise ValueError("query_code must define a 'query' function")

    try:
        result = query_fn(question, artifacts)
        return result if isinstance(result, dict) else {}
    except Exception as exc:
        logger.warning("query_failed question=%s error=%s", question[:80], exc)
        return {}


# ═════════════════════════════════════════════════════════════════════════════
# Bootstrap functions
# ═════════════════════════════════════════════════════════════════════════════


async def bootstrap(
    domain_description: str,
    eval_set: list[EvalItem],
    router: LLMRouter,
) -> tuple[str, str, str]:
    """LLM proposes initial schema, curate, and query code.

    Returns (schema_code, curate_code, query_code).
    """
    prompt = BOOTSTRAP_SCHEMA_PROMPT.format(
        domain_description=domain_description,
        seed_questions=_seed_questions_text(eval_set[:5]),
        skill_signatures=_skill_signatures_text(),
    )

    request = LLMRequest(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=4096,
    )

    response = await router.complete(request)
    return _parse_llm_response(response.content)


async def refine(
    domain_description: str,
    schema_code: str,
    curate_code: str,
    query_code: str,
    failure_report: FailureReport,
    iteration: int,
    router: LLMRouter,
) -> tuple[str, str, str]:
    """LLM proposes refined code based on structured failure analysis.

    Returns (schema_code, curate_code, query_code).
    """
    prompt = REFINE_PROMPT.format(
        domain_description=domain_description,
        schema_code=schema_code,
        curate_code=curate_code,
        query_code=query_code,
        failure_report_json=_failure_report_json(failure_report),
        skill_signatures=_skill_signatures_text(),
        iteration=iteration,
    )

    request = LLMRequest(
        messages=[{"role": "user", "content": prompt}],
        temperature=0.0,
        max_tokens=4096,
    )

    response = await router.complete(request)
    return _parse_llm_response(response.content)


# ═════════════════════════════════════════════════════════════════════════════
# Grade
# ═════════════════════════════════════════════════════════════════════════════


async def grade(
    results: list[dict[str, Any]],
    eval_set: list[EvalItem],
    router: LLMRouter,
    failure_threshold: float = 0.7,
) -> tuple[float, FailureReport]:
    """Grade all eval results and produce a score + failure report.

    Scoring: max(exact_match, llm_judge_score). Failures below
    failure_threshold are classified and bucketed by difficulty.
    """
    scores: list[float] = []
    failures: list[FailureCase] = []

    for result, item in zip(results, eval_set):
        exact = exact_match(result, item.answer)
        if exact:
            score = 1.0
        else:
            llm_score = await llm_judge(result, item.answer, item.question, router)
            score = max(0.0, float(llm_score))

        scores.append(score)

        if score < failure_threshold:
            failure = FailureCase(
                question_id=item.id,
                difficulty=item.difficulty,
                expected=item.answer,
                got=result,
                artifacts_used=[],  # populated downstream if available
                failure_class=classify_failure(result, item),
            )
            failures.append(failure)

    by_difficulty: dict[str, list[FailureCase]] = {}
    for f in failures:
        by_difficulty.setdefault(f.difficulty, []).append(f)

    overall = statistics.mean(scores) if scores else 0.0
    return overall, FailureReport(failures=failures, by_difficulty=by_difficulty)


def compute_scores_by_difficulty(
    results: list[dict[str, Any]],
    eval_set: list[EvalItem],
) -> dict[str, float]:
    """Compute per-difficulty average scores (exact match only, no LLM judge)."""
    by_difficulty: dict[str, list[float]] = {}
    for result, item in zip(results, eval_set):
        exact = 1.0 if exact_match(result, item.answer) else 0.0
        by_difficulty.setdefault(item.difficulty, []).append(exact)

    return {
        diff: statistics.mean(s) if s else 0.0
        for diff, s in by_difficulty.items()
    }


# ═════════════════════════════════════════════════════════════════════════════
# Main harness
# ═════════════════════════════════════════════════════════════════════════════


async def compile_context(
    domain_description: str,
    eval_set: list[EvalItem],
    corpus: list[ParsedDoc],
    router: LLMRouter,
    max_iters: int = 15,
    threshold: float = 0.85,
) -> CompiledContext:
    """Eval-driven compiler loop — auto-tunes extraction per domain.

    Bootstrap → curate → evaluate → grade → refine loop.  Returns best-
    achieved context if threshold is met, or the best context found with
    its score surfaced to the operator.

    Args:
        domain_description: 1-3 sentence description of the domain.
        eval_set: 50-200 (question, ground_truth) pairs.
        corpus: Layer 1 parsed documents for the domain.
        router: LLM router for bootstrap, refine, and judging.
        max_iters: Maximum refinement iterations (default 15).
        threshold: Target accuracy score (default 0.85).

    Returns:
        CompiledContext with the best-achieved schema/curate/query code
        and score metadata.
    """
    # Bootstrap: LLM proposes initial code from domain description + seed items
    logger.info("compiler_bootstrap domain=%s", domain_description[:60])
    schema_code, curate_code, query_code = await bootstrap(
        domain_description, eval_set, router
    )

    best_score = 0.0
    best_context: CompiledContext | None = None

    for iteration in range(max_iters):
        logger.info("compiler_iteration iter=%d/%d", iteration + 1, max_iters)

        # Curate: compile artifacts from corpus using current code
        try:
            artifacts = run_curate(curate_code, corpus)
        except Exception as exc:
            logger.warning("curation_failed iter=%d error=%s", iteration, exc)
            artifacts = []

        if not artifacts:
            logger.warning("no_artifacts iter=%d — refining without eval", iteration)
            # Still attempt to refine if no artifacts produced
            failure_report = FailureReport(
                failures=[
                    FailureCase(
                        question_id=item.id,
                        difficulty=item.difficulty,
                        expected=item.answer,
                        got={},
                        artifacts_used=[],
                        failure_class=FailureClass.MISSING_ARTIFACT,
                    )
                    for item in eval_set[:5]  # Only seed items for failure report
                ],
                by_difficulty={
                    item.difficulty: [
                        FailureCase(
                            question_id=item.id,
                            difficulty=item.difficulty,
                            expected=item.answer,
                            got={},
                            artifacts_used=[],
                            failure_class=FailureClass.MISSING_ARTIFACT,
                        )
                    ]
                    for item in eval_set[:5]
                    if item.difficulty
                },
            )
        else:
            # Evaluate: run all eval queries against compiled artifacts
            results = [run_query(query_code, item.question, artifacts)
                       for item in eval_set]

            # Grade: LLM judge + exact match
            score, failure_report = await grade(results, eval_set, router)

            logger.info(
                "compiler_score iter=%d score=%.3f best=%.3f failures=%d",
                iteration,
                score,
                best_score,
                len(failure_report.failures),
            )

            if score > best_score:
                best_score = score
                scores_by_diff = compute_scores_by_difficulty(results, eval_set)
                best_context = CompiledContext(
                    schema_code=schema_code,
                    curate_code=curate_code,
                    query_code=query_code,
                    score=score,
                    scores_by_difficulty=scores_by_diff,
                    iterations=iteration + 1,
                )

            if score >= threshold:
                logger.info("compiler_converged iter=%d score=%.3f", iteration, score)
                return best_context  # type: ignore[return-value]

        # Refine: LLM reads failures and proposes code diffs
        try:
            schema_code, curate_code, query_code = await refine(
                domain_description,
                schema_code,
                curate_code,
                query_code,
                failure_report,
                iteration,
                router,
            )
        except Exception as exc:
            logger.warning("refine_failed iter=%d error=%s", iteration, exc)
            # Continue with current code if refine fails

    # Return best achieved even if threshold not met
    if best_context is None:
        # Never produced a valid context — return a minimal one
        best_context = CompiledContext(
            schema_code=schema_code,
            curate_code=curate_code,
            query_code=query_code,
            score=0.0,
            scores_by_difficulty={},
            iterations=max_iters,
        )

    logger.info(
        "compiler_done score=%.3f threshold=%.3f iterations=%d",
        best_score,
        threshold,
        max_iters,
    )
    return best_context