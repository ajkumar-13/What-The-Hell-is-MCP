# A container for the system-information server in code/05-first-server.
#
# The build context is that project, not this directory:
#
#   docker build -f code/21-deploy/Dockerfile -t mcp-system-info:0.2.0 code/05-first-server
#
# Two stages. The build stage owns uv and the toolchain; the runtime stage owns
# a virtual environment and nothing else. Neither uv nor a compiler survives
# into the image that faces the network.

# ---------------------------------------------------------------------------
# Stage 1: resolve and install dependencies from the committed lockfile.
# ---------------------------------------------------------------------------
# Pinned to a Debian release, not to the floating `python:3.12-slim` tag. The
# stronger form is a digest pin; see README.md for the one command that turns
# this line into one.
FROM python:3.12-slim-bookworm AS build

# uv is pinned too. An unpinned `:latest` here makes the build unreproducible
# in exactly the way the lockfile was supposed to prevent.
COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /src

# Dependencies first, in their own layer. Editing a tool body then rebuilds one
# layer instead of re-resolving the dependency tree.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project --no-editable

# Then the project itself. `--no-editable` puts a real copy of the package in
# the virtual environment, so the runtime stage does not need the source tree.
COPY README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------
# Stage 2: runtime.
# ---------------------------------------------------------------------------
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

# A system account with no login shell and no home of its own to write into.
RUN useradd --system --no-create-home --shell /usr/sbin/nologin --uid 10001 mcp

WORKDIR /app
COPY --from=build --chown=mcp:mcp /src/.venv /app/.venv

USER 10001
EXPOSE 8000

# Liveness only: the port is bound and something is accepting connections. It
# deliberately does not speak MCP. Readiness is a different question, and this
# server has no route that answers it; README.md explains what to add when
# yours does.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import socket; socket.create_connection(('127.0.0.1', 8000), 2).close()"]

# 0.0.0.0 is required inside a container, and it has a consequence: the SDK
# only auto-arms DNS-rebinding protection when the bind address is loopback, so
# Origin validation is OFF here. Terminate TLS and validate Origin in front of
# this container, or pass TransportSecuritySettings in the server. See
# README.md and post 21.
CMD ["python", "-m", "system_info", "--http", "--host", "0.0.0.0", "--port", "8000"]
