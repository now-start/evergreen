# syntax=docker/dockerfile:1.7

FROM python:3.12.13-slim-bookworm AS base

WORKDIR /app

FROM base AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY src/main ./src/main
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

FROM base AS runtime

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN groupadd --gid 10001 evergreen \
    && useradd --uid 10001 --gid evergreen --no-create-home \
        --no-log-init --shell /usr/sbin/nologin evergreen

COPY --from=builder --chown=10001:10001 /app/.venv ./.venv

USER 10001:10001

EXPOSE 8080 8081

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/actuator/health', timeout=3).close()"]

CMD ["evergreen"]
