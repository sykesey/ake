from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.local", env_file_encoding="utf-8")

    # Database
    database_url: str = "postgresql+asyncpg://ake:ake@localhost/ake"
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False

    # LLM Router
    llm_provider: str = "anthropic"
    llm_model: str = "claude-sonnet-4-6"
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_max_retries: int = 3
    llm_timeout_seconds: int = 120

    # Fallback chain: comma-separated "provider/model" strings tried in order on failure
    llm_fallback_chain: str = ""

    # MCP
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    mcp_sse_port: int = 8001

    # MCP SSL (optional — set to enable HTTPS on the SSE transport)
    mcp_ssl_certfile: str | None = None
    mcp_ssl_keyfile: str | None = None
    mcp_ssl_keyfile_password: str | None = None

    # Observability
    log_level: str = "INFO"
    trace_store_url: str | None = None


settings = Settings()
