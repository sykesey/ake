# F000 — Infrastructure, Wiring & Base System

**Status:** Defined  
**Layer:** 0 — Foundation (prerequisite for all other layers)

## Statement

The system provides a production-ready Python 3.10+ project scaffold, a fully containerised local development environment, a cloud-portable configuration model, and a provider-agnostic LLM router with first-class tool calling support — so that every higher layer is built on a consistent, testable, and deployable base from day one.

---

## 1 — Python Project Structure

### Runtime

- Python **3.10+** required. Use `3.12` as the pinned dev version; the constraint is `>=3.10` in `pyproject.toml`.
- Package manager: **[uv](https://github.com/astral-sh/uv)** for dependency resolution and virtual environment management. `uv.lock` is committed; `requirements.txt` is not.

### `pyproject.toml` layout

```toml
[project]
name = "amorphous-knowledge-engine"
version = "0.1.0"
requires-python = ">=3.10"

dependencies = [
    "asyncpg>=0.29",            # async Postgres driver
    "sqlalchemy[asyncio]>=2.0", # async ORM + core
    "alembic>=1.13",            # schema migrations
    "pgvector>=0.3",            # pgvector SQLAlchemy type
    "pydantic>=2.7",            # data models and validation
    "litellm>=1.40",            # LLM provider routing
    "mcp>=1.0",                 # MCP server + client SDK
    "tenacity>=8.3",            # retry / backoff
    "structlog>=24.0",          # structured logging
    "python-dotenv>=1.0",       # .env loading for local dev
]

[dependency-groups]
ingestion = [
    "unstructured[pdf,docx]>=0.14",  # document parsing
    "pyarrow>=16",                    # Parquet / Arrow
    "rdflib>=7",                      # RDF ingestion
    "networkx>=3",                    # graph utilities
]
dev = [
    "pytest>=8",
    "pytest-asyncio>=0.23",
    "pytest-postgresql>=6",     # real Postgres fixture
    "httpx>=0.27",              # async HTTP for integration tests
    "ruff>=0.4",
    "mypy>=1.10",
]
```

### Directory layout additions (beyond dev guide)

```
knowledge-engine/
├── ake/                      # importable package root
│   ├── config.py             # Settings (pydantic-settings)
│   ├── db/
│   │   ├── engine.py         # async engine + session factory
│   │   ├── migrations/       # Alembic env + revision files
│   │   └── schema.sql        # canonical DDL (source of truth)
│   ├── llm/
│   │   ├── router.py         # LLMRouter (see section 5)
│   │   ├── tools.py          # ToolRegistry + ToolDefinition
│   │   └── mcp_bridge.py     # MCP tool discovery + proxy
│   ├── mcp/                  # MCP server (F011)
│   └── ...                   # layers 1–4 as per dev guide
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml
├── pyproject.toml
└── uv.lock
```

---

## 2 — Database Infrastructure

### Postgres + pgvector

- **Engine:** Postgres 16, `pgvector` extension enabled.
- **Driver:** `asyncpg` via SQLAlchemy's async engine. All database operations are `async`/`await`; no synchronous DB calls anywhere in the stack.
- **Connection pool:** SQLAlchemy `AsyncEngine` with `pool_size=10`, `max_overflow=20`, `pool_pre_ping=True`. Pool parameters are overridable via config (see section 4).

### Migrations

Alembic manages all schema changes. Rules:
- `store/schema.sql` is the canonical human-readable DDL and is kept in sync with Alembic revisions.
- Migrations are **never destructive in a single step**: column removal requires a two-migration deprecation cycle (add → release → remove).
- `alembic upgrade head` runs automatically inside the Docker entrypoint for local dev and as an init container for cloud deployments.
- The `pgvector` extension is created in the baseline migration: `CREATE EXTENSION IF NOT EXISTS vector;`

### Session management

```python
# ake/db/engine.py
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from ake.config import settings

engine = create_async_engine(
    settings.database_url,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_pre_ping=True,
    echo=settings.db_echo,
)

AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

async def get_session() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session
```

`get_session` is the only entry point for database access. No code outside `store/` holds a raw connection.

### RLS session variable

Every session that touches the `artifacts` table must set `app.current_principals` before any query. This is enforced by a SQLAlchemy event listener on checkout — it is not left to callers:

```python
@event.listens_for(engine.sync_engine, "connect")
def set_search_path(dbapi_conn, _):
    dbapi_conn.execute("SET app.current_principals = '{}'")
```

The query layer overwrites this per-request before executing.

---

## 3 — Containerisation

### Dockerfile (multi-stage)

```dockerfile
# Stage 1: dependency builder
FROM python:3.12-slim AS builder
WORKDIR /build
RUN pip install uv
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --group ingestion

# Stage 2: runtime image
FROM python:3.12-slim AS runtime
WORKDIR /app
COPY --from=builder /build/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
COPY ake/ ./ake/
COPY docker/entrypoint.sh ./

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

ENTRYPOINT ["./entrypoint.sh"]
```

`entrypoint.sh` runs `alembic upgrade head` then starts the MCP server (or any specified command).

### docker-compose (local dev)

```yaml
services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_DB: ake
      POSTGRES_USER: ake
      POSTGRES_PASSWORD: ake
    ports: ["5432:5432"]
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ake"]
      interval: 5s
      retries: 10

  ake:
    build:
      context: ..
      dockerfile: docker/Dockerfile
    depends_on:
      postgres:
        condition: service_healthy
    environment:
      DATABASE_URL: postgresql+asyncpg://ake:ake@postgres:5432/ake
      LLM_PROVIDER: anthropic
      LLM_MODEL: claude-sonnet-4-6
    env_file: [.env.local]
    ports: ["8000:8000"]
    volumes:
      - ./ake:/app/ake   # live reload in dev

  # Optional: local LLM for offline development
  ollama:
    image: ollama/ollama:latest
    profiles: [offline]
    ports: ["11434:11434"]
    volumes:
      - ollama_models:/root/.ollama

volumes:
  pgdata:
  ollama_models:
```

---

## 4 — Cloud Environment & Configuration

### 12-factor config model

All configuration is read from environment variables. No config files are bundled in the image. `pydantic-settings` is used so that settings are typed and validated at startup:

```python
# ake/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8")

    # Database
    database_url: str                      # postgresql+asyncpg://...
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # LLM Router
    llm_provider: str = "anthropic"        # anthropic | openai | azure | litellm_proxy | ollama
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str | None = None         # omit for env-authenticated clouds
    llm_base_url: str | None = None        # override for compatible endpoints
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 120

    # Fallback chain (comma-separated model strings, attempted in order)
    llm_fallback_chain: str = ""

    # MCP
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000

    # Observability
    log_level: str = "INFO"
    trace_store_url: str | None = None     # if set, traces are written here

settings = Settings()
```

### Cloud targets

The same image runs on any OCI-compatible runtime. Tested deployment targets:

| Target | Postgres | Secrets |
|---|---|---|
| Local Docker | pgvector/pgvector:pg16 | `.env.local` |
| AWS | RDS Postgres 16 + pgvector, or Aurora Serverless v2 | AWS Secrets Manager → env via ECS task definition |
| GCP | Cloud SQL Postgres 16 + pgvector | Secret Manager → env via Cloud Run |
| Fly.io | Fly Postgres (pgvector pre-installed) | `fly secrets set` |
| Neon / Supabase | Managed Postgres with pgvector | Platform secrets → `DATABASE_URL` |

`DATABASE_URL` is the only required variable. All other settings have safe defaults. There is no cloud-provider SDK in the AKE package; secret injection is the platform's responsibility.

### Health & readiness

Two HTTP endpoints are served alongside the MCP server:

- `GET /health` — liveness; returns `200` if the process is running
- `GET /ready` — readiness; returns `200` only after Alembic migrations are complete and the database connection pool is warm. Returns `503` until then.

---

## 5 — LLM Router

The `LLMRouter` in `ake/llm/router.py` is the single entry point for all LLM calls in the system. No code outside `ake/llm/` calls a provider SDK directly.

### Design

- **Provider translation** is delegated to **LiteLLM** — it handles the API surface differences between Anthropic, OpenAI, Azure OpenAI, and any OpenAI-compatible endpoint.
- **Tool calling dispatch** is owned by `LLMRouter` — it converts AKE `ToolDefinition` objects to provider-specific tool schemas, detects tool call responses, dispatches to registered handlers, and feeds results back in an agentic loop.
- **MCP tool integration** is handled by `MCPBridge` — it discovers tools from connected MCP servers, wraps them as `ToolDefinition` objects with a remote handler, and registers them in the router transparently.

### Interfaces

```python
# ake/llm/tools.py

from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

@dataclass
class ToolDefinition:
    name: str
    description: str
    input_schema: dict                              # JSON Schema for tool input
    handler: Callable[..., Awaitable[Any]] | None   # None = external/MCP tool
    source: str = "internal"                        # "internal" | "mcp:{server_name}"
    annotations: dict = field(default_factory=dict) # arbitrary metadata


class ToolRegistry:
    def register(self, tool: ToolDefinition) -> None: ...
    def get(self, name: str) -> ToolDefinition | None: ...
    def all(self) -> list[ToolDefinition]: ...
    def as_provider_schema(self, provider: str) -> list[dict]: ...
    # Translates to {"type":"function","function":{...}} for OpenAI
    # or {"name":..., "description":..., "input_schema":{...}} for Anthropic
```

```python
# ake/llm/router.py

from dataclasses import dataclass
from typing import Any, AsyncIterator

@dataclass
class LLMRequest:
    messages: list[dict]          # OpenAI-style message list
    tools: list[str] = ()         # tool names from the registry to expose; empty = none
    system: str | None = None
    temperature: float = 0.0
    max_tokens: int = 4096
    stream: bool = False
    model: str | None = None      # overrides settings.llm_model for this call

@dataclass
class LLMResponse:
    content: str
    tool_calls_made: list[dict]   # log of all tool calls and their results
    input_tokens: int
    output_tokens: int
    model_used: str
    provider_used: str

class LLMRouter:
    def __init__(self, registry: ToolRegistry, settings: Settings): ...

    async def complete(self, request: LLMRequest) -> LLMResponse:
        """
        Runs the full agentic loop:
          1. Call provider with messages + tools (translated to provider schema)
          2. If response contains tool calls, dispatch each to its handler
          3. Append tool results to messages and loop
          4. Return when the provider emits a text response (no tool calls)
        Max loop iterations: 10 (configurable). Raises ToolLoopError on overflow.
        """
        ...

    async def stream(self, request: LLMRequest) -> AsyncIterator[str]:
        """Streaming variant; tool call dispatch still happens synchronously mid-stream."""
        ...
```

### Agentic tool loop

```
LLMRouter.complete(request)
  │
  ├─► translate tools → provider schema
  ├─► call provider (via LiteLLM)
  │
  ├─ if response has tool_calls:
  │     for each tool_call:
  │       ├─ look up ToolDefinition in registry
  │       ├─ validate input against input_schema
  │       ├─ if handler is not None: await handler(**args)
  │       └─ if handler is None (MCP): forward via MCPBridge
  │     append tool results to messages
  │     loop ──────────────────────────────────────────────┐
  │                                                        │
  └─ if response is text: return LLMResponse  ◄───────────┘
```

### Provider configuration examples

```bash
# Anthropic (default)
LLM_PROVIDER=anthropic
LLM_MODEL=claude-sonnet-4-6
LLM_API_KEY=sk-ant-...

# OpenAI
LLM_PROVIDER=openai
LLM_MODEL=gpt-4o
LLM_API_KEY=sk-...

# Azure OpenAI
LLM_PROVIDER=azure
LLM_MODEL=gpt-4o          # deployment name
LLM_BASE_URL=https://<resource>.openai.azure.com/
LLM_API_KEY=<azure-key>

# Any OpenAI-compatible endpoint (Ollama, vLLM, Together, Groq, etc.)
LLM_PROVIDER=openai
LLM_BASE_URL=http://localhost:11434/v1
LLM_MODEL=llama3.2
LLM_API_KEY=ollama         # required by LiteLLM even if unused

# Fallback chain (tried in order on rate-limit or error)
LLM_FALLBACK_CHAIN=anthropic/claude-haiku-4-5-20251001,openai/gpt-4o-mini
```

### Retry & resilience

- Retries via `tenacity`: exponential backoff, max `settings.llm_max_retries` attempts, retried on rate-limit (429) and transient server errors (5xx).
- Timeout: `settings.llm_timeout_seconds` applied per provider call, not per loop iteration.
- Fallback chain: if all retries for the primary model are exhausted, LiteLLM attempts each model in `LLM_FALLBACK_CHAIN` in order. Fallback model used is recorded in `LLMResponse.model_used`.
- Every call emits a structured log entry with `provider`, `model`, `input_tokens`, `output_tokens`, `latency_ms`, `tool_calls_made`, and `fallback_used`.

---

## 6 — MCP Tool Bridge

`ake/llm/mcp_bridge.py` connects the LLM router to external MCP servers so their tools are first-class participants in tool-calling loops.

```python
class MCPBridge:
    async def connect(self, server_name: str, transport: str, **kwargs) -> None:
        """Connect to an MCP server (stdio or SSE transport)."""
        ...

    async def discover_tools(self, server_name: str) -> list[ToolDefinition]:
        """
        Call list_tools on the connected server.
        Returns ToolDefinition objects with source="mcp:{server_name}"
        and handler=self._make_proxy(server_name, tool_name).
        """
        ...

    async def register_all(self, registry: ToolRegistry) -> None:
        """Discover tools from all connected servers and register them."""
        ...
```

AKE's own MCP server (F011) is connected via `MCPBridge` at startup so the LLM router can invoke AKE's own tools (`ake_query`, `ake_describe_schema`, etc.) in the same loop as any other tool. This means the extraction LLM in the compiler loop can call `ake_query` as a tool if needed, without special-casing.

---

## Acceptance Criteria

### Python environment
- [ ] `uv sync` installs all dependencies from a clean environment without errors on macOS and Linux
- [ ] `mypy ake/` passes with zero errors (strict mode for `ake/llm/` and `ake/db/`; standard for ingestion parsers)
- [ ] `ruff check ake/` passes with zero errors
- [ ] `pytest tests/` passes with a live Postgres fixture (using `pytest-postgresql`)

### Database
- [ ] `alembic upgrade head` runs on a fresh Postgres 16 instance and produces the correct schema including `pgvector` extension, all tables, indexes, and RLS policy
- [ ] `alembic downgrade -1` / `alembic upgrade head` cycle completes without data loss on a seeded database
- [ ] Connection pool exhaustion is handled gracefully (queued, not errored) up to `max_overflow`

### Docker
- [ ] `docker compose up` starts postgres and ake services; `GET /ready` returns 200 within 30 seconds
- [ ] `docker compose --profile offline up` additionally starts Ollama; LLM calls route through it when `LLM_BASE_URL=http://ollama:11434/v1`
- [ ] The runtime image is < 800 MB uncompressed

### Configuration
- [ ] Starting with only `DATABASE_URL` and `LLM_API_KEY` set, the service starts and serves `/health` and `/ready`
- [ ] An invalid `DATABASE_URL` causes a startup failure with a clear error message before any request is served
- [ ] All settings are documented in a `.env.example` file at the repository root

### LLM Router
- [ ] `LLMRouter.complete()` produces a correct response for a basic prompt against Anthropic, OpenAI, and an Ollama local endpoint without code changes (config only)
- [ ] A tool call loop terminates correctly: LLM requests a tool → handler is called → result is fed back → LLM produces a final text response
- [ ] MCP tools discovered via `MCPBridge` are invokable in the same loop as internal tools
- [ ] On provider rate-limit (simulated with a mock), the router retries with backoff and falls back to the next model in the fallback chain
- [ ] `LLMResponse.tool_calls_made` contains a complete log of all tool calls and their return values for the completed request
- [ ] Tool input that fails JSON Schema validation raises `ToolInputValidationError` before calling the handler

## Out of Scope

- Authentication and authorisation for the MCP server HTTP surface (platform-level concern; API key middleware is added in F005's cloud hardening phase)
- GPU-accelerated local inference (Ollama runs on CPU by default; GPU passthrough is a deployment configuration)
- Multi-region or active-active database setup
- Message queue / async job infrastructure for long-running ingestion (ingestion is synchronous in this phase; a job queue is added when async ingestion demand is confirmed)
