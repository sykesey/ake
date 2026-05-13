"""Unit tests for the eval-driven compiler loop (F007).
Covers all pure functions: exact_match, classify_failure, code parsing,
grader, failure bucketing, and the compile_context harness.
"""
from __future__ import annotations

import json
import textwrap
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ake.compiler.compiler_loop import (
    BOOTSTRAP_SCHEMA_PROMPT,
    REFINE_PROMPT,
    CompiledContext,
    EvalItem,
    FailureCase,
    FailureClass,
    FailureReport,
    ParsedDoc,
    _exec_code,
    _extract_code_block,
    _failure_report_json,
    _parse_llm_response,
    _seed_questions_text,
    _skill_signatures_text,
    bootstrap,
    classify_failure,
    compile_context,
    compute_scores_by_difficulty,
    exact_match,
    grade,
    llm_judge,
    refine,
    run_curate,
    run_query,
)
from ake.llm.router import LLMResponse


# ═════════════════════════════════════════════════════════════════════════════
# exact_match
# ═════════════════════════════════════════════════════════════════════════════


class TestExactMatch:
    def test_identical_flat_dicts(self):
        assert exact_match({"a": 1, "b": 2}, {"a": 1, "b": 2}) is True

    def test_different_values(self):
        assert exact_match({"a": 1}, {"a": 2}) is False

    def test_different_keys(self):
        assert exact_match({"a": 1}, {"b": 1}) is False

    def test_extra_keys(self):
        assert exact_match({"a": 1, "b": 2}, {"a": 1}) is False

    def test_missing_keys(self):
        assert exact_match({"a": 1}, {"a": 1, "b": 2}) is False

    def test_nested_dicts_match(self):
        assert exact_match(
            {"a": {"x": 1, "y": 2}, "b": 3},
            {"a": {"x": 1, "y": 2}, "b": 3},
        ) is True

    def test_nested_dicts_differ(self):
        assert exact_match(
            {"a": {"x": 1, "y": 2}},
            {"a": {"x": 1, "y": 3}},
        ) is False

    def test_lists_match(self):
        assert exact_match({"items": [1, 2, 3]}, {"items": [1, 2, 3]}) is True

    def test_lists_differ(self):
        assert exact_match({"items": [1, 2]}, {"items": [1, 2, 3]}) is False

    def test_type_mismatch(self):
        assert exact_match({"a": 1}, {"a": "1"}) is False

    def test_none_values(self):
        assert exact_match({"a": None}, {"a": None}) is True

    def test_empty_dicts(self):
        assert exact_match({}, {}) is True

    def test_float_int_mismatch(self):
        """1 and 1.0 are not equal in exact_match."""
        assert exact_match({"a": 1.0}, {"a": 1}) is False


# ═════════════════════════════════════════════════════════════════════════════
# Failure classification
# ═════════════════════════════════════════════════════════════════════════════


class TestClassifyFailure:
    def test_empty_result_is_missing_artifact(self):
        item = EvalItem(id="q1", question="Q?", answer={"val": 100.0}, difficulty="single_fact")
        assert classify_failure({}, item) == FailureClass.MISSING_ARTIFACT

    def test_all_none_is_missing_artifact(self):
        item = EvalItem(id="q1", question="Q?", answer={"val": 100.0}, difficulty="single_fact")
        assert classify_failure({"val": None}, item) == FailureClass.MISSING_ARTIFACT

    def test_unit_divergence_10x(self):
        item = EvalItem(id="q1", question="Q?", answer={"val": 100.0}, difficulty="single_fact")
        assert classify_failure({"val": 10.0}, item) == FailureClass.UNIT_ERROR

    def test_unit_divergence_100x_approx(self):
        item = EvalItem(id="q1", question="Q?", answer={"val": 1.0}, difficulty="single_fact")
        assert classify_failure({"val": 100.0}, item) == FailureClass.UNIT_ERROR

    def test_unit_divergence_1000x(self):
        item = EvalItem(id="q1", question="Q?", answer={"val": 1_000_000.0}, difficulty="single_fact")
        assert classify_failure({"val": 1.0}, item) == FailureClass.UNIT_ERROR

    def test_multi_entity_missing_entity(self):
        item = EvalItem(
            id="q1",
            question="Q?",
            answer={"NVDA": 100, "MSFT": 200},
            difficulty="multi_company",
        )
        assert classify_failure({"NVDA": 100}, item) == FailureClass.MULTI_ENTITY_MISS

    def test_wrong_granularity_too_few_fields(self):
        item = EvalItem(
            id="q1",
            question="Q?",
            answer={"a": 1, "b": 2, "c": 3},
            difficulty="single_fact",
        )
        assert classify_failure({"a": 1}, item) == FailureClass.WRONG_GRANULARITY

    def test_wrong_values_is_section_miss(self):
        item = EvalItem(id="q1", question="Q?", answer={"val": 100}, difficulty="single_fact")
        assert classify_failure({"val": 50}, item) == FailureClass.SECTION_MISS

    def test_citation_gap_all_nulls_in_expected_fields(self):
        """Citation gap: extraction partially works (other fields have values)
        but the expected fields are null — couldn't ground them."""
        item = EvalItem(id="q1", question="Q?", answer={"a": 100, "b": 200}, difficulty="single_fact")
        # Got has non-null fields, but expected fields a and b are null
        assert classify_failure({"a": None, "b": None, "other": 42}, item) == FailureClass.CITATION_GAP

    def test_nonexistent_diff_value_not_unit(self):
        """A value difference that isn't a power-of-10 should be section_miss."""
        item = EvalItem(id="q1", question="Q?", answer={"val": 100}, difficulty="single_fact")
        assert classify_failure({"val": 70}, item) == FailureClass.SECTION_MISS


# ═════════════════════════════════════════════════════════════════════════════
# FailureReport serialization
# ═════════════════════════════════════════════════════════════════════════════


class TestFailureReportJson:
    def test_serializes_correctly(self):
        report = FailureReport(
            failures=[
                FailureCase(
                    question_id="q1",
                    difficulty="single_fact",
                    expected={"v": 100.0},
                    got={"v": 10.0},
                    artifacts_used=[],
                    failure_class=FailureClass.UNIT_ERROR,
                ),
                FailureCase(
                    question_id="q2",
                    difficulty="multi_company",
                    expected={"NVDA": 100, "MSFT": 200},
                    got={"NVDA": 100},
                    artifacts_used=[],
                    failure_class=FailureClass.MULTI_ENTITY_MISS,
                ),
            ],
            by_difficulty={
                "single_fact": [
                    FailureCase(
                        question_id="q1",
                        difficulty="single_fact",
                        expected={"v": 100.0},
                        got={"v": 10.0},
                        artifacts_used=[],
                        failure_class=FailureClass.UNIT_ERROR,
                    ),
                ],
                "multi_company": [
                    FailureCase(
                        question_id="q2",
                        difficulty="multi_company",
                        expected={"NVDA": 100, "MSFT": 200},
                        got={"NVDA": 100},
                        artifacts_used=[],
                        failure_class=FailureClass.MULTI_ENTITY_MISS,
                    ),
                ],
            },
        )

        result = json.loads(_failure_report_json(report))
        assert result["total_failures"] == 2
        assert result["failures_by_difficulty"]["single_fact"] == 1
        assert result["failures_by_difficulty"]["multi_company"] == 1
        assert result["failure_details"][0]["failure_class"] == "unit_error"
        assert result["failure_details"][1]["failure_class"] == "multi_entity_miss"


# ═════════════════════════════════════════════════════════════════════════════
# Skill signatures and seed questions
# ═════════════════════════════════════════════════════════════════════════════


class TestSkillSignatures:
    def test_returns_non_empty_string(self):
        text = _skill_signatures_text()
        assert len(text) > 100
        assert "extract_table" in text
        assert "find_section" in text
        assert "normalize_currency" in text

    def test_contains_all_seven_skills(self):
        text = _skill_signatures_text()
        for name in (
            "extract_table",
            "find_section",
            "extract_named_entities",
            "normalize_currency",
            "normalize_date",
            "locate_by_proximity",
            "resolve_entity",
        ):
            assert name in text


class TestSeedQuestions:
    def test_formats_items_correctly(self):
        items = [
            EvalItem(id="q1", question="What is X?", answer={"v": 100}, difficulty="single_fact"),
            EvalItem(id="q2", question="Compare Y?", answer={"a": 1, "b": 2}, difficulty="multi_company"),
        ]
        text = _seed_questions_text(items)
        assert "What is X?" in text
        assert '"v": 100' in text
        assert "Compare Y?" in text
        assert "single_fact" in text
        assert "multi_company" in text

    def test_truncates_to_five(self):
        items = [
            EvalItem(id=f"q{i}", question=f"Q{i}", answer={"v": i}, difficulty="single_fact")
            for i in range(10)
        ]
        text = _seed_questions_text(items)
        assert "Q5" not in text
        assert "Q4" in text


# ═════════════════════════════════════════════════════════════════════════════
# Code block extraction
# ═════════════════════════════════════════════════════════════════════════════


class TestExtractCodeBlock:
    def test_extracts_marked_block(self):
        text = "```schema\nfrom dataclasses import dataclass\nschema = DomainSchema(...)\n```"
        result = _extract_code_block(text, "schema")
        assert "from dataclasses import dataclass" in result
        assert "DomainSchema" in result

    def test_extracts_with_python_tag(self):
        text = "```python\ncode block\n```"
        result = _extract_code_block(text, "schema")
        assert result == "code block"

    def test_raises_on_no_code_block(self):
        with pytest.raises(ValueError, match="Could not extract"):
            _extract_code_block("no code here", "schema")

    def test_extract_with_newlines(self):
        text = "```schema\n\nclass Schema:\n    pass\n\n```"
        result = _extract_code_block(text, "schema")
        assert "class Schema:" in result

    def test_multi_block_returns_correctly(self):
        text = textwrap.dedent("""\
            ```schema
            schema_part
            ```
            some text
            ```curate
            curate_part
            ```
            ```query
            query_part
            ```
        """)
        assert "schema_part" in _extract_code_block(text, "schema")
        assert "curate_part" in _extract_code_block(text, "curate")
        assert "query_part" in _extract_code_block(text, "query")


class TestParseLlmResponse:
    def test_parses_all_three_blocks(self):
        text = textwrap.dedent("""\
            ```schema
            schema_code
            ```
            ```curate
            curate_code
            ```
            ```query
            query_code
            ```
        """)
        schema, curate, query = _parse_llm_response(text)
        assert "schema_code" in schema
        assert "curate_code" in curate
        assert "query_code" in query


# ═════════════════════════════════════════════════════════════════════════════
# Code execution sandbox
# ═════════════════════════════════════════════════════════════════════════════


class TestExecCode:
    def test_executes_simple_function(self):
        code = """
def add(a, b):
    return a + b
"""
        ns = _exec_code(code)
        assert "add" in ns
        assert ns["add"](1, 2) == 3

    def test_executes_with_imports(self):
        code = """
import json
def to_json(d):
    return json.dumps(d)
"""
        ns = _exec_code(code)
        result = ns["to_json"]({"key": "value"})
        assert json.loads(result) == {"key": "value"}


class TestRunCurate:
    def test_runs_curate_across_docs(self):
        code = """
def curate(doc):
    return [{"doc_id": doc.doc_id, "element_count": len(doc.elements)}]
"""
        docs = [
            ParsedDoc(doc_id="d1", elements=[]),
            ParsedDoc(doc_id="d2", elements=[]),
        ]
        results = run_curate(code, docs)
        assert len(results) == 2
        assert results[0]["doc_id"] == "d1"
        assert results[1]["doc_id"] == "d2"

    def test_no_curate_fn_raises(self):
        code = """
def other_fn():
    pass
"""
        with pytest.raises(ValueError, match="curate_code must define a 'curate' function"):
            run_curate(code, [ParsedDoc(doc_id="d1", elements=[])])

    def test_curate_exception_is_caught(self):
        code = """
def curate(doc):
    raise RuntimeError("boom")
"""
        docs = [ParsedDoc(doc_id="d1", elements=[])]
        result = run_curate(code, docs)
        assert result == []

    def test_curate_returns_none_skipped(self):
        code = """
def curate(doc):
    return None
"""
        docs = [ParsedDoc(doc_id="d1", elements=[])]
        result = run_curate(code, docs)
        assert result == []


class TestRunQuery:
    def test_runs_query_function(self):
        code = """
def query(question, artifacts):
    return {"answer": question, "count": len(artifacts)}
"""
        result = run_query(code, "test question", [{"a": 1}, {"b": 2}])
        assert result == {"answer": "test question", "count": 2}

    def test_no_query_fn_raises(self):
        code = """
def other():
    pass
"""
        with pytest.raises(ValueError, match="query_code must define a 'query' function"):
            run_query(code, "test", [])

    def test_query_returns_non_dict(self):
        code = """
def query(question, artifacts):
    return "not a dict"
"""
        result = run_query(code, "test", [])
        assert result == {}

    def test_query_exception_returns_empty(self):
        code = """
def query(question, artifacts):
    raise RuntimeError("boom")
"""
        result = run_query(code, "test", [])
        assert result == {}


# ═════════════════════════════════════════════════════════════════════════════
# Grade
# ═════════════════════════════════════════════════════════════════════════════


class TestGrade:
    @pytest.mark.asyncio
    async def test_all_exact_matches(self):
        router = MagicMock()
        items = [
            EvalItem(id="q1", question="Q1", answer={"v": 1}, difficulty="single_fact"),
            EvalItem(id="q2", question="Q2", answer={"v": 2}, difficulty="single_fact"),
        ]
        results = [{"v": 1}, {"v": 2}]
        score, report = await grade(results, items, router)
        assert score == 1.0
        assert len(report.failures) == 0

    @pytest.mark.asyncio
    async def test_mixed_with_llm_judge(self):
        router = MagicMock()
        router.complete = AsyncMock(
            return_value=LLMResponse(
                content="0.8",
                tool_calls_made=[],
                input_tokens=10,
                output_tokens=5,
                model_used="test",
                provider_used="test",
            )
        )
        items = [
            EvalItem(id="q1", question="Q1", answer={"v": 1}, difficulty="single_fact"),
        ]
        results = [{"v": 2}]  # not exact match
        score, report = await grade(results, items, router)
        assert score == 0.8

    @pytest.mark.asyncio
    async def test_handles_non_float_llm_response(self):
        router = MagicMock()
        router.complete = AsyncMock(
            return_value=LLMResponse(
                content="The score is 0.75 approximately.",
                tool_calls_made=[],
                input_tokens=10,
                output_tokens=10,
                model_used="test",
                provider_used="test",
            )
        )
        items = [
            EvalItem(id="q1", question="Q1", answer={"v": 1}, difficulty="single_fact"),
        ]
        results = [{"v": 2}]
        score, report = await grade(results, items, router)
        assert score == 0.75

    @pytest.mark.asyncio
    async def test_buckets_by_difficulty(self):
        router = MagicMock()
        router.complete = AsyncMock(
            return_value=LLMResponse(
                content="0.5",
                tool_calls_made=[],
                input_tokens=10,
                output_tokens=5,
                model_used="test",
                provider_used="test",
            )
        )
        items = [
            EvalItem(id="q1", question="Q?", answer={"v": 1}, difficulty="single_fact"),
            EvalItem(id="q2", question="Q?", answer={"NVDA": 1, "MSFT": 2}, difficulty="multi_company"),
        ]
        results = [{}, {}]  # all missing, no exact match, triggers LLM judge
        score, report = await grade(results, items, router)
        assert len(report.failures) == 2
        assert "single_fact" in report.by_difficulty
        assert "multi_company" in report.by_difficulty
        assert len(report.by_difficulty["single_fact"]) == 1

    @pytest.mark.asyncio
    async def test_clamps_llm_score(self):
        router = MagicMock()
        router.complete = AsyncMock(
            return_value=LLMResponse(
                content="1.5",
                tool_calls_made=[],
                input_tokens=10,
                output_tokens=5,
                model_used="test",
                provider_used="test",
            )
        )
        items = [EvalItem(id="q1", question="Q?", answer={"v": 1}, difficulty="single_fact")]
        results = [{"v": 2}]
        score, _ = await grade(results, items, router)
        assert score == 1.0  # clamped to 1.0


class TestComputeScoresByDifficulty:
    def test_returns_per_difficulty_scores(self):
        items = [
            EvalItem(id="q1", question="Q?", answer={"v": 1}, difficulty="single_fact"),
            EvalItem(id="q2", question="Q?", answer={"v": 2}, difficulty="single_fact"),
            EvalItem(id="q3", question="Q?", answer={"v": 3}, difficulty="multi_company"),
        ]
        results = [{"v": 1}, {"v": 2}, {"v": 999}]
        scores = compute_scores_by_difficulty(results, items)
        assert scores["single_fact"] == 1.0
        assert scores["multi_company"] == 0.0


# ═════════════════════════════════════════════════════════════════════════════
# Bootstrap and refine (prompt generation)
# ═════════════════════════════════════════════════════════════════════════════


class TestBootstrapPrompt:
    def test_prompt_formatting(self):
        prompt = BOOTSTRAP_SCHEMA_PROMPT.format(
            domain_description="Financial reports",
            seed_questions="Q1\nA1\nQ2\nA2",
            skill_signatures="extract_table: ...",
        )
        assert "Financial reports" in prompt
        assert "Q1" in prompt
        assert "extract_table" in prompt
        assert "DomainSchema" in prompt
        assert "curate" in prompt
        assert "query" in prompt

    def test_prompt_includes_code_block_markers(self):
        prompt = BOOTSTRAP_SCHEMA_PROMPT.format(
            domain_description="Test",
            seed_questions="Q",
            skill_signatures="S",
        )
        assert "```schema" in prompt
        assert "```curate" in prompt
        assert "```query" in prompt


class TestRefinePrompt:
    def test_prompt_formatting(self):
        prompt = REFINE_PROMPT.format(
            domain_description="Financial reports",
            schema_code="schema code",
            curate_code="curate code",
            query_code="query code",
            failure_report_json='{"failures": []}',
            skill_signatures="extract_table: ...",
            iteration=3,
        )
        assert "Financial reports" in prompt
        assert "schema code" in prompt
        assert "curate code" in prompt
        assert "iteration 3" in prompt
        assert '{"failures": []}' in prompt


# ═════════════════════════════════════════════════════════════════════════════
# compile_context integration
# ═════════════════════════════════════════════════════════════════════════════


class TestCompileContext:
    @pytest.mark.asyncio
    async def test_bootstrap_returns_context(self):
        """Verify compile_context returns a context even without real LLM calls."""
        router = MagicMock()

        # Mock bootstrap to return valid python code blocks
        bootstrap_response = textwrap.dedent("""\
            ```schema
            from ake.compiler.artifact import DomainSchema, FieldSpec
            schema = DomainSchema(
                artifact_type="test",
                description="Test schema",
                entity_id_field="entity",
                fields={"value": FieldSpec(description="A value", type="float")}
            )
            ```
            ```curate
            def curate(doc):
                return [{"doc_id": doc.doc_id, "value": 42.0}]
            ```
            ```query
            def query(question, artifacts):
                if artifacts:
                    return {"value": artifacts[0].get("value", 0)}
                return {}
            ```
        """)
        router.complete = AsyncMock(return_value=LLMResponse(
            content=bootstrap_response,
            tool_calls_made=[],
            input_tokens=100,
            output_tokens=200,
            model_used="test",
            provider_used="test",
        ))

        eval_set = [
            EvalItem(id="q1", question="Q?", answer={"value": 42.0}, difficulty="single_fact"),
        ]
        corpus = [ParsedDoc(doc_id="d1", elements=[])]

        context = await compile_context(
            domain_description="Test domain",
            eval_set=eval_set,
            corpus=corpus,
            router=router,
            max_iters=1,
            threshold=1.0,
        )

        assert isinstance(context, CompiledContext)
        assert context.schema_code is not None
        assert context.curate_code is not None
        assert context.query_code is not None
        assert context.score >= 0.0
        assert context.iterations > 0

    @pytest.mark.asyncio
    async def test_returns_best_context_on_max_iters(self):
        """When threshold is never met, returns best context found."""
        router = MagicMock()

        response1 = textwrap.dedent("""\
            ```schema
            schema code v1
            ```
            ```curate
            def curate(doc):
                return [{"value": 0.5}]
            ```
            ```query
            def query(question, artifacts):
                return {"value": 0.5}
            ```
        """)

        response2 = textwrap.dedent("""\
            ```schema
            schema code v2
            ```
            ```curate
            def curate(doc):
                return [{"value": 0.9}]
            ```
            ```query
            def query(question, artifacts):
                return {"value": 1}
            ```
        """)

        router.complete = AsyncMock(side_effect=[
            LLMResponse(content=response1, tool_calls_made=[], input_tokens=10, output_tokens=10, model_used="t", provider_used="t"),
            # First grade — no exact match, triggers judge
            LLMResponse(content="0.5", tool_calls_made=[], input_tokens=10, output_tokens=5, model_used="t", provider_used="t"),
            # First refine
            LLMResponse(content=response2, tool_calls_made=[], input_tokens=10, output_tokens=10, model_used="t", provider_used="t"),
            # Second grade
            LLMResponse(content="0.9", tool_calls_made=[], input_tokens=10, output_tokens=5, model_used="t", provider_used="t"),
        ])

        eval_set = [
            EvalItem(id="q1", question="Q?", answer={"value": 1}, difficulty="single_fact"),
        ]
        corpus = [ParsedDoc(doc_id="d1", elements=[])]

        context = await compile_context(
            domain_description="Test",
            eval_set=eval_set,
            corpus=corpus,
            router=router,
            max_iters=2,
            threshold=1.0,
        )

        assert context.score >= 0.5
        assert "schema code v2" in context.schema_code

    @pytest.mark.asyncio
    async def test_converges_when_threshold_met(self):
        """Early exit when score reaches threshold."""
        router = MagicMock()

        response = textwrap.dedent("""\
            ```schema
            s
            ```
            ```curate
            def curate(doc):
                return [{"value": 100.0}]
            ```
            ```query
            def query(question, artifacts):
                return {"value": 100.0}
            ```
        """)

        router.complete = AsyncMock(return_value=LLMResponse(
            content=response,
            tool_calls_made=[],
            input_tokens=10,
            output_tokens=10,
            model_used="t",
            provider_used="t",
        ))

        eval_set = [
            EvalItem(id="q1", question="Q?", answer={"value": 100.0}, difficulty="single_fact"),
        ]
        corpus = [ParsedDoc(doc_id="d1", elements=[])]

        context = await compile_context(
            domain_description="Test",
            eval_set=eval_set,
            corpus=corpus,
            router=router,
            max_iters=5,
            threshold=0.85,
        )

        assert context.score == 1.0

    @pytest.mark.asyncio
    async def test_no_artifacts_still_refines(self):
        """When curate produces no artifacts, still creates a failure report and refines."""
        router = MagicMock()

        # First bootstrap produces valid-looking code
        bootstrap_response = textwrap.dedent("""\
            ```schema
            s
            ```
            ```curate
            def curate(doc):
                pass
            ```
            ```query
            def query(question, artifacts):
                return {}
            ```
        """)

        refine_response = textwrap.dedent("""\
            ```schema
            s2
            ```
            ```curate
            def curate(doc):
                return [{"value": 100.0}]
            ```
            ```query
            def query(question, artifacts):
                return {"value": 100.0}
            ```
        """)

        router.complete = AsyncMock(side_effect=[
            LLMResponse(content=bootstrap_response, tool_calls_made=[], input_tokens=10, output_tokens=10, model_used="t", provider_used="t"),
            LLMResponse(content=refine_response, tool_calls_made=[], input_tokens=10, output_tokens=10, model_used="t", provider_used="t"),
        ])

        eval_set = [
            EvalItem(id="q1", question="Q?", answer={"value": 100.0}, difficulty="single_fact"),
        ]
        corpus = [ParsedDoc(doc_id="d1", elements=[])]

        context = await compile_context(
            domain_description="Test",
            eval_set=eval_set,
            corpus=corpus,
            router=router,
            max_iters=2,
            threshold=1.0,
        )

        assert isinstance(context, CompiledContext)


# ═════════════════════════════════════════════════════════════════════════════
# CompiledContext
# ═════════════════════════════════════════════════════════════════════════════


class TestCompiledContext:
    def test_fields_are_populated(self):
        ctx = CompiledContext(
            schema_code="s",
            curate_code="c",
            query_code="q",
            score=0.92,
            scores_by_difficulty={"single_fact": 0.95, "multi_company": 0.89},
            iterations=7,
        )
        assert ctx.schema_code == "s"
        assert ctx.curate_code == "c"
        assert ctx.query_code == "q"
        assert ctx.score == 0.92
        assert ctx.scores_by_difficulty["single_fact"] == 0.95
        assert ctx.iterations == 7