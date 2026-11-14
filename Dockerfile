# ==============================================================================
# Multi-stage Dockerfile for GoSmart DICOM Graph Server
# Base image: Python 3.14 (Debian Slim)
# Managed with Astral uv
# ==============================================================================

# ------------------------------------------------------------------------------
# Stage 1: Builder
# ------------------------------------------------------------------------------
FROM python:3.14-slim AS builder

# Prevent interactive prompts during apt install
ENV DEBIAN_FRONTEND=noninteractive \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

# Install build dependencies for C-extensions (argon2-cffi, cryptography, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Install Astral uv binary from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency manifests first to leverage Docker layer caching
COPY pyproject.toml uv.lock ./

# Install locked production dependencies without installing the root project itself
RUN uv sync --frozen --no-dev --no-install-project

# Copy project source and metadata needed by build backend (hatchling)
COPY README.md LICENSE ./
COPY src/ ./src/

# Install root project into virtual environment
RUN uv sync --frozen --no-dev

# ------------------------------------------------------------------------------
# Stage 2: Runtime Runner
# ------------------------------------------------------------------------------
FROM python:3.14-slim AS runner

LABEL maintainer="GoSmart Health" \
      description="GoSmart DICOM Knowledge Graph Server on Python 3.14"

# Runtime system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Create unprivileged application user and group (UID/GID 10001)
RUN groupadd --gid 10001 appuser \
    && useradd --uid 10001 --gid 10001 --create-home --shell /bin/bash appuser

WORKDIR /app

# Copy virtual environment and project artifacts from builder stage
COPY --from=builder --chown=appuser:appuser /app /app

# Environment configuration
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    GOSMART_GS_HOST=0.0.0.0 \
    GOSMART_GS_PORT=8086 \
    GOSMART_GS_RELOAD=false \
    GOSMART_GS_STORAGE_ROOT_URI=file:///tmp/gosmart_dicom_storage

# Pre-create local storage and log directories with proper permissions
RUN mkdir -p /tmp/gosmart_dicom_storage /app/logs \
    && chown -R appuser:appuser /tmp/gosmart_dicom_storage /app/logs

# Switch to unprivileged runtime user
USER appuser

# Expose ports:
# 8086  - FastAPI HTTP / Swagger / ReDoc / MCP SSE / StreamableHTTP
# 11112 - DICOM C-STORE / C-ECHO SCP Server
EXPOSE 8086
EXPOSE 11112

# Health check to ensure the REST server is responsive
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8086/api/v1/health || exit 1

# Start Uvicorn server
CMD ["uvicorn", "main:app", "--app-dir", "src", "--host", "0.0.0.0", "--port", "8086"]
