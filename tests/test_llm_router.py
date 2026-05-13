"""Tests for LLMRouter — all provider calls are mocked so no API key is needed."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ake.llm.router import LLMRequest, LLMResponse, LLMRouter, ToolInputValidationError, ToolLoopError
from ake.llm.tools import ToolDefinition, ToolRegistry


# ── Helpers ──────────────────────────────────────────────────────────────────


def _make_text_response(content: str, model: str = "claude-sonnet-4-6") -> MagicMock:
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = None
    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "stop"
    resp = MagicMock()
    resp.choices = [choice]
    resp.model = model
    resp.usage.prompt_tokens = 10
    resp.usage.completion_tokens = 5
    return resp


def _make_tool_response(name: str, args: dict, tool_id: str = "tc_1") -> MagicMock:
    tc = MagicMock()
    tc.id = tool_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)

    msg = MagicMock()
    msg.content = None
    msg.tool_calls = [tc]

    choice = MagicMock()
    choice.message = msg
    choice.finish_reason = "tool_calls"

    resp = MagicMock()
    resp.choices = [choice]
    resp.model = "claude-sonnet-4-6"
    resp.usage.prompt_tokens = 20
    resp.usage.completion_tokens = 10
    return resp


def _make_router(registry: ToolRegistry) -> LLMRouter:
    from ake.config import Settings

    cfg = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        llm_provider="anthropic",
        llm_model="claude-sonnet-4-6",
        llm_api_key="sk-test",
        llm_max_retries=1,
    )
    return LLMRouter(registry, settings=cfg)


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_completion(registry: ToolRegistry) -> None:
    router = _make_router(registry)
    mock_response = _make_text_response("Hello, world!")

    with patch("litellm.acompletion", new=AsyncMock(return_value=mock_response)):
        result = await router.complete(LLMRequest(messages=[{"role": "user", "content": "Hi"}]))

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello, world!"
    assert result.tool_calls_made == []
    assert result.input_tokens == 10
    assert result.output_tokens == 5


@pytest.mark.asyncio
async def test_tool_call_loop(registry: ToolRegistry) -> None:
    handler = AsyncMock(return_value={"temperature": 22})
    registry.register(
        ToolDefinition(
            name="get_weather",
            description="Get temperature",
            input_schema={
                "type": "object",
                "properties": {"city": {"type": "string"}},
                "required": ["city"],
            },
            handler=handler,
        )
    )
    router = _make_router(registry)

    tool_resp = _make_tool_response("get_weather", {"city": "London"})
    text_resp = _make_text_response("The temperature is 22°C.")

    with patch(
        "litellm.acompletion", new=AsyncMock(side_effect=[tool_resp, text_resp])
    ):
        result = await router.complete(
            LLMRequest(
                messages=[{"role": "user", "content": "What's the weather?"}],
                tools=["get_weather"],
            )
        )

    assert result.content == "The temperature is 22°C."
    assert len(result.tool_calls_made) == 1
    log = result.tool_calls_made[0]
    assert log["tool"] == "get_weather"
    assert log["input"] == {"city": "London"}
    assert log["result"] == {"temperature": 22}
    handler.assert_awaited_once_with(city="London")


@pytest.mark.asyncio
async def test_tool_input_validation_error(registry: ToolRegistry) -> None:
    """Handler must NOT be called when input fails JSON Schema validation."""
    handler = AsyncMock()
    registry.register(
        ToolDefinition(
            name="strict_tool",
            description="Requires an integer",
            input_schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
            handler=handler,
        )
    )
    router = _make_router(registry)

    # Send invalid args (string instead of integer)
    tool_resp = _make_tool_response("strict_tool", {"count": "not-an-int"})

    with patch("litellm.acompletion", new=AsyncMock(return_value=tool_resp)):
        with pytest.raises(ToolInputValidationError):
            await router.complete(
                LLMRequest(
                    messages=[{"role": "user", "content": "Go"}],
                    tools=["strict_tool"],
                )
            )

    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_tool_loop_overflow(registry: ToolRegistry) -> None:
    """Router must raise ToolLoopError after MAX_LOOP_ITERATIONS tool responses."""
    handler = AsyncMock(return_value={"done": False})
    registry.register(
        ToolDefinition(
            name="loopy",
            description="Always returns tool calls",
            input_schema={"type": "object", "properties": {}},
            handler=handler,
        )
    )
    router = _make_router(registry)
    always_tool = _make_tool_response("loopy", {}, "tc_loop")

    with patch(
        "litellm.acompletion",
        new=AsyncMock(return_value=always_tool),
    ):
        with pytest.raises(ToolLoopError):
            await router.complete(
                LLMRequest(messages=[{"role": "user", "content": "Loop"}], tools=["loopy"])
            )


@pytest.mark.asyncio
async def test_system_prompt_prepended(registry: ToolRegistry) -> None:
    router = _make_router(registry)
    captured: list[list[dict]] = []

    async def _capture(**kwargs):
        captured.append(kwargs["messages"])
        return _make_text_response("ok")

    with patch("litellm.acompletion", new=_capture):
        await router.complete(
            LLMRequest(
                messages=[{"role": "user", "content": "Hi"}],
                system="You are helpful.",
            )
        )

    assert captured[0][0] == {"role": "system", "content": "You are helpful."}
    assert captured[0][1] == {"role": "user", "content": "Hi"}


@pytest.mark.asyncio
async def test_fallback_chain_on_rate_limit(registry: ToolRegistry) -> None:
    from ake.config import Settings

    cfg = Settings(
        database_url="postgresql+asyncpg://x:x@localhost/x",
        llm_provider="anthropic",
        llm_model="claude-sonnet-4-6",
        llm_api_key="sk-test",
        llm_max_retries=1,
        llm_fallback_chain="openai/gpt-4o-mini",
    )
    router = LLMRouter(registry, settings=cfg)

    fallback_resp = _make_text_response("fallback answer", model="gpt-4o-mini")
    call_models: list[str] = []

    async def _mock_call_provider(**kwargs):
        model: str = kwargs.get("model", "")
        call_models.append(model)
        if "claude" in model:
            # Raise a generic exception; _complete_with_fallback catches any Exception.
            raise RuntimeError("simulated rate limit")
        return fallback_resp

    # Patch at the router-instance level so we bypass tenacity retry noise.
    router._call_provider = _mock_call_provider  # type: ignore[method-assign]

    result = await router.complete(LLMRequest(messages=[{"role": "user", "content": "Hi"}]))

    assert result.content == "fallback answer"
    assert any("gpt-4o-mini" in m for m in call_models)
