FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Dependency layer (cached unless lockfile changes)
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

# Smoke gate available in the deployed machine (railway ssh → run it to test
# whether the platform's egress IP passes the four anti-bots).
COPY scripts ./scripts

# SQLite path: attach a Railway Volume at /data (dashboard → Service → Volumes).
ENV UCUZU_DB_PATH=/data/ucuzu.db
RUN mkdir -p /data
CMD ["uv", "run", "--no-sync", "python", "-m", "ucuzu"]