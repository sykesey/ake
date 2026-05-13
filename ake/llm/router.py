from __future__ import annotations

import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import jsonschema
import litellm
import structlog
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from ake.config import Settings
from ake.config import settings as _default_settings
from ake.llm.tools import ToolDefinition, ToolRegistry

logger = structlog.get_logger()


class ToolLoopError(Exception):
    pass


class ToolInputValidationError(Exception):
    pass


@dataclass
class LLMRequest:
    messages: list[dict]
    tools: list[str] = field(default_factory=list)
    system: str | None = None
    temperature: float = 0.0
    max_tokens: int = 4096
    stream: bool = False
    model: str | None = None


@dataclass
class LLMResponse:
    content: str
    tool_calls_made: list[dict]
    input_tokens: int
    output_tokens: int
    model_used: str
    provider_used: str


class LLMRouter:
    MAX_LOOP_ITERATIONS = 10

    def __init__(
        self,
        registry: ToolRegistry,
        settings: Settings = _default_settings,
    ) -> None:
        self._registry = registry
        self._settings = settings

    def _model_string(self, override: str | None = None) -> str:
        m = override or self._settings.llm_model
        if "/" in m:
            return m
        provider = self._settings.llm_provider
        if provider in ("anthropic", "openai", "azure", "ollama"):
            return f"{provider}/{m}" if provider != "azure" else m
        return m

    def _tools_schema(self, names: list[str]) -> list[dict]:
        schema: list[dict] = []
        for name in names:
            tool = self._registry.get(name)
            if tool is None:
                continue
            schema.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
            )
        return schema

    def _validate_input(self, tool: ToolDefinition, args: dict[str, Any]) -> None:
        try:
            jsonschema.validate(args, tool.input_schema)
        except jsonschema.ValidationError as exc:
            raise ToolInputValidationError(
                f"Tool '{tool.name}' input validation failed: {exc.message}"
            ) from exc

    async def _dispatch(self, name: str, arguments_json: str) -> Any:
        args: dict[str, Any] = json.loads(arguments_json)
        tool = self._registry.get(name)
        if tool is None:
            return {"error": f"unknown tool: {name}"}
        self._validate_input(tool, args)
        if tool.handler is not None:
            return await tool.handler(**args)
        return {"error": f"no handler registered for tool '{name}'"}

    async def _call_provider(self, **kwargs: Any) -> Any:
        retryable = (
            litellm.RateLimitError,
            litellm.ServiceUnavailableError,
            litellm.APIConnectionError,
        )
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._settings.llm_max_retries),
            wait=wait_exponential(multiplier=1, min=1, max=60),
            retry=retry_if_exception_type(retryable),
            reraise=True,
        ):
            with attempt:
                return await litellm.acompletion(**kwargs)

    def _base_kwargs(self, model: str, messages: list[dict], request: LLMRequest) -> dict[str, Any]:
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=messages,
            temperature=request.temperature,
            max_tokens=request.max_tokens,
            timeout=self._settings.llm_timeout_seconds,
        )
        if self._settings.llm_api_key:
            kwargs["api_key"] = self._settings.llm_api_key
        if self._settings.llm_base_url:
            kwargs["base_url"] = self._settings.llm_base_url
        if request.tools:
            kwargs["tools"] = self._tools_schema(request.tools)
        return kwargs

    async def _complete_with_fallback(self, **kwargs: Any) -> Any:
        try:
            return await self._call_provider(**kwargs)
        except Exception:
            for entry in self._settings.llm_fallback_chain.split(","):
                fallback = entry.strip()
                if not fallback:
                    continue
                try:
                    return await self._call_provider(**{**kwargs, "model": fallback})
                except Exception:
                    continue
            raise

    async def complete(self, request: LLMRequest) -> LLMResponse:
        messages = list(request.messages)
        if request.system:
            messages = [{"role": "system", "content": request.system}, *messages]

        model = self._model_string(request.model)
        tool_calls_log: list[dict] = []
        total_input = total_output = 0
        model_used = model

        for _ in range(self.MAX_LOOP_ITERATIONS):
            kwargs = self._base_kwargs(model, messages, request)
            t0 = time.monotonic()
            response = await self._complete_with_fallback(**kwargs)
            latency_ms = int((time.monotonic() - t0) * 1000)

            usage = getattr(response, "usage", None)
            if usage:
                total_input += getattr(usage, "prompt_tokens", 0)
                total_output += getattr(usage, "completion_tokens", 0)

            model_used = getattr(response, "model", model) or model
            choice = response.choices[0]
            msg = choice.message

            logger.info(
                "llm_call",
                provider=self._settings.llm_provider,
                model=model_used,
                input_tokens=total_input,
                output_tokens=total_output,
                latency_ms=latency_ms,
                tool_calls_made=len(tool_calls_log),
            )

            if not (getattr(msg, "tool_calls", None)):
                return LLMResponse(
                    content=msg.content or "",
                    tool_calls_made=tool_calls_log,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    model_used=model_used,
                    provider_used=self._settings.llm_provider,
                )

            # Append assistant turn with tool calls, then dispatch each.
            messages.append(
                {
                    "role": "assistant",
                    "content": msg.content,
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                        }
                        for tc in msg.tool_calls
                    ],
                }
            )

            for tc in msg.tool_calls:
                result = await self._dispatch(tc.function.name, tc.function.arguments)
                tool_calls_log.append(
                    {
                        "tool": tc.function.name,
                        "input": json.loads(tc.function.arguments),
                        "result": result,
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(result),
                    }
                )

        raise ToolLoopError(f"Tool loop exceeded {self.MAX_LOOP_ITERATIONS} iterations")

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        """Streaming variant. Tool call dispatch is synchronous mid-stream;
        text tokens are yielded as they arrive from the provider."""
        messages = list(request.messages)
        if request.system:
            messages = [{"role": "system", "content": request.system}, *messages]

        model = self._model_string(request.model)

        for _ in range(self.MAX_LOOP_ITERATIONS):
            kwargs = self._base_kwargs(model, messages, request)
            kwargs["stream"] = True

            response = await litellm.acompletion(**kwargs)

            accumulated_content = ""
            accumulated_tool_calls: dict[int, dict] = {}

            async for chunk in response:
                delta = chunk.choices[0].delta
                if delta.content:
                    accumulated_content += delta.content
                    yield delta.content
                if getattr(delta, "tool_calls", None):
                    for tc_chunk in delta.tool_calls:
                        idx: int = tc_chunk.index
                        if idx not in accumulated_tool_calls:
                            accumulated_tool_calls[idx] = {
                                "id": tc_chunk.id or "",
                                "function": {"name": tc_chunk.function.name or "", "arguments": ""},
                            }
                        if tc_chunk.id:
                            accumulated_tool_calls[idx]["id"] = tc_chunk.id
                        if tc_chunk.function.name:
                            accumulated_tool_calls[idx]["function"]["name"] = tc_chunk.function.name
                        if tc_chunk.function.arguments:
                            accumulated_tool_calls[idx]["function"]["arguments"] += (
                                tc_chunk.function.arguments
                            )

            if not accumulated_tool_calls:
                return

            tool_calls = [accumulated_tool_calls[i] for i in sorted(accumulated_tool_calls)]
            messages.append(
                {
                    "role": "assistant",
                    "content": accumulated_content or None,
                    "tool_calls": [
                        {"id": tc["id"], "type": "function", "function": tc["function"]}
                        for tc in tool_calls
                    ],
                }
            )
            for tc in tool_calls:
                result = await self._dispatch(tc["function"]["name"], tc["function"]["arguments"])
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc["id"],
                        "content": json.dumps(result),
                    }
                )

        raise ToolLoopError(f"Tool loop exceeded {self.MAX_LOOP_ITERATIONS} iterations")
