FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# Install uv (astral-sh/uv)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY README.md SKILL.md AGENTS.md notebooklm-py.png ./
# Fix DNS and IPv6 issues for uv in Docker Desktop
ENV UV_NATIVE_TLS=1 \
    UV_HTTP_TIMEOUT=300

# Install project and browser dependencies using uv
RUN uv sync --extra browser

# Expose MCP port
EXPOSE 8000

# Set environment variables
ENV PORT=8000
ENV HOST=0.0.0.0
CMD ["uv", "run", "notebooklm-mcp"]
