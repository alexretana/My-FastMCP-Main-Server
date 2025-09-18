# Multi-stage build for MCP Proxy Server
FROM python:3.11-slim as builder

# Install uv for fast dependency management
RUN pip install uv

# Set working directory
WORKDIR /app

# Copy pyproject.toml, README.md and source code
COPY pyproject.toml README.md ./
COPY src/ /app/src/

# Install dependencies
RUN uv venv && \
    uv pip install -e .

# Production stage
FROM python:3.11-slim as production

# Create non-root user for security
RUN groupadd -r mcp && useradd -r -g mcp mcp

# Install system dependencies
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        curl \
        git \
        ca-certificates && \
    rm -rf /var/lib/apt/lists/*

# Copy virtual environment from builder
COPY --from=builder /app/.venv /app/.venv

# Copy application code
COPY src/ /app/src/
COPY pyproject.toml README.md /app/

# Set up environment
ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/src"

# Create directories
RUN mkdir -p /config /data /var/log && \
    chown -R mcp:mcp /config /data /var/log /app

# Switch to non-root user
USER mcp

# Set working directory
WORKDIR /app

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:53456/health || exit 1

# Expose port
EXPOSE 53456

# Default command
CMD ["mcp-proxy", "run", "--config", "/config/docker.json"]