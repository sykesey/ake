from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Awaitable[Any]] | None
    source: str = "internal"
    annotations: dict = field(default_factory=dict)


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def all(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def as_provider_schema(self, provider: str = "openai") -> list[dict]:
        """Translate registered tools to the wire format expected by the given provider.

        LiteLLM accepts OpenAI format and converts internally, so "openai" is the
        safe default for all LiteLLM-routed calls.
        """
        result: list[dict] = []
        for tool in self._tools.values():
            if provider == "anthropic":
                result.append(
                    {
                        "name": tool.name,
                        "description": tool.description,
                        "input_schema": tool.input_schema,
                    }
                )
            else:
                result.append(
                    {
                        "type": "function",
                        "function": {
                            "name": tool.name,
                            "description": tool.description,
                            "parameters": tool.input_schema,
                        },
                    }
                )
        return result
