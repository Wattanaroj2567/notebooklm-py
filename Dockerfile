FROM python:3.12-slim



WORKDIR /app

# Copy dependency files
COPY pyproject.toml uv.lock ./
COPY src/ ./src/
COPY README.md SKILL.md AGENTS.md notebooklm-py.png ./
# Fix DNS and IPv6 issues for uv in Docker Desktop
ENV UV_SYSTEM_CERTS=1 \
    UV_HTTP_TIMEOUT=300

# Install project and browser dependencies using pip with PyPI mirror
RUN pip install --no-cache-dir --timeout 300 --index-url https://pypi.org/simple .[browser]

# Expose MCP port
EXPOSE 8000

# Set environment variables
ENV PORT=8000
ENV HOST=0.0.0.0
CMD ["notebooklm-mcp"]
