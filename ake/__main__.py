"""AKE process entry point.

Runs database migrations, warms the connection pool, then serves health/ready
endpoints. The MCP server is started here once F011 is implemented.

Usage:
    python -m ake
    uv run python -m ake
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import sys

import structlog

from ake import server as health_server
from ake.config import settings
from ake.db.engine import engine

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(settings.log_level.upper())
    ),
)

log = structlog.get_logger()


def _run_migrations() -> None:
    log.info("running_migrations")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        log.error("migration_failed", stderr=result.stderr)
        sys.exit(1)
    log.info("migrations_complete")


async def _warm_pool() -> None:
    async with engine.connect() as conn:
        await conn.execute(__import__("sqlalchemy").text("SELECT 1"))
    log.info("db_pool_warmed")


async def main() -> None:
    _run_migrations()
    await _warm_pool()

    health_server.mark_ready()
    health_server.start(host=settings.mcp_host, port=settings.mcp_port)
    log.info("ake_ready", host=settings.mcp_host, port=settings.mcp_port)

    # Block until interrupted. F011 replaces this with the MCP server loop.
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
