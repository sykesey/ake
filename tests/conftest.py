"""Test configuration and shared fixtures.

DATABASE_URL and LLM_API_KEY are set before any ake module import so that
pydantic-settings can validate them at module load time.
"""
from __future__ import annotations

import os

# Must come before any ake import.
os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://ake:ake@localhost:5432/ake_test")
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_PROVIDER", "anthropic")
os.environ.setdefault("LLM_MODEL", "claude-sonnet-4-6")

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from ake.llm.tools import ToolRegistry


# ── LLM / Registry fixtures ──────────────────────────────────────────────────


@pytest.fixture
def registry() -> ToolRegistry:
    return ToolRegistry()


# ── Database fixtures (require a live Postgres with pgvector) ─────────────────
#
# These fixtures are skipped automatically when DATABASE_URL points to a
# non-reachable host.  CI should supply a real Postgres 16 + pgvector
# instance via pytest-postgresql or a docker-compose service.

@pytest_asyncio.fixture
async def db_engine():
    from ake.db.engine import engine
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncSession:
    SessionLocal = async_sessionmaker(db_engine, expire_on_commit=False)
    async with SessionLocal() as session:
        yield session
