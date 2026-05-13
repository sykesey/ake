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
COPY alembic.ini ./
COPY docker/entrypoint.sh ./

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import httpx; httpx.get('http://localhost:8000/health').raise_for_status()"

ENTRYPOINT ["./entrypoint.sh"]
CMD ["python", "-m", "ake"]
