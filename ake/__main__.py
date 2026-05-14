"""AKE process entry point.

Runs database migrations, warms the connection pool, then launches the MCP
server in either SSE or stdio mode (F011).  The health/ready HTTP server runs
on a separate port for container orchestration.

Usage:
    python -m ake --sse                          # SSE mode (default, port 8001)
    python -m ake --sse --sse-port 8080          # SSE on custom port
    python -m ake --sse --sse-host 0.0.0.0       # SSE on custom host
    python -m ake --sse --ssl-certfile cert.pem --ssl-keyfile key.pem  # HTTPS
    python -m ake --stdio                        # stdio mode (Claude Desktop)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys

import structlog

from ake import server as health_server
from ake.config import settings
from ake.db.engine import engine
from ake.mcp import run_sse, run_stdio

structlog.configure(
    wrapper_class=structlog.make_filtering_bound_logger(
        logging.getLevelName(settings.log_level.upper())
    ),
)

log = structlog.get_logger()


def _run_migrations() -> None:
    log.info("running_migrations")
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
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


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Amorphous Knowledge Engine — MCP server",
    )
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument(
        "--sse",
        action="store_true",
        help="Run MCP server over SSE (default)",
    )
    mode_group.add_argument(
        "--stdio",
        action="store_true",
        dest="stdio",
        help="Run MCP server over stdio (for Claude Desktop)",
    )
    parser.add_argument(
        "--sse-host",
        default=settings.mcp_host,
        help=f"Host for SSE server (default: {settings.mcp_host})",
    )
    parser.add_argument(
        "--sse-port",
        type=int,
        default=settings.mcp_sse_port,
        help=f"Port for SSE server (default: {settings.mcp_sse_port})",
    )
    parser.add_argument(
        "--ssl-certfile",
        default=settings.mcp_ssl_certfile,
        help="Path to SSL certificate file (enables HTTPS on SSE transport)",
    )
    parser.add_argument(
        "--ssl-keyfile",
        default=settings.mcp_ssl_keyfile,
        help="Path to SSL private key file",
    )
    parser.add_argument(
        "--ssl-keyfile-password",
        default=settings.mcp_ssl_keyfile_password,
        help="Password for the SSL private key file, if encrypted",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(argv)

    _run_migrations()
    asyncio.run(_warm_pool())

    # Health server for container orchestration (port 8000)
    health_server.mark_ready()
    health_server.start(host=settings.mcp_host, port=settings.mcp_port)
    log.info("health_server_ready", host=settings.mcp_host, port=settings.mcp_port)

    if args.stdio:
        log.info("mcp_stdio_starting")
        run_stdio()
    else:
        log.info(
            "mcp_sse_starting",
            host=args.sse_host,
            port=args.sse_port,
            ssl=bool(args.ssl_certfile),
        )
        run_sse(
            host=args.sse_host,
            port=args.sse_port,
            ssl_certfile=args.ssl_certfile,
            ssl_keyfile=args.ssl_keyfile,
            ssl_keyfile_password=args.ssl_keyfile_password,
        )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass