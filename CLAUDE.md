# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

This is the MCP Proxy Server, a FastMCP-based application that aggregates multiple MCP (Model Context Protocol) servers into a single endpoint. The proxy allows clients like Claude Desktop to access multiple MCP servers through one unified interface.

## Development Commands

### Running the Application
```bash
# Install in development mode
uv pip install -e .

# Run the proxy server
mcp-proxy run --config config.json

# Create a configuration template
mcp-proxy create-config --deployment uv --with-credentials

# Validate configuration
mcp-proxy validate --config config.json --check-credentials
```

### Testing
```bash
# Run tests
pytest

# Run tests with coverage
pytest --cov=mcp_proxy_server --cov-report=html

# Run specific test types
pytest -m unit        # Unit tests only
pytest -m integration # Integration tests only
pytest -m slow        # Slow tests only
```

### Code Quality
```bash
# Format code
black src/
isort src/

# Type checking
mypy src/

# Linting
flake8 src/
```

## Architecture

### Core Components

1. **FastMCP Proxy Server** (`src/mcp_proxy_server/proxy/server.py`)
   - Main orchestrator handling client requests
   - Routes tool/resource calls to appropriate backend servers
   - Manages ProxyClient instances for each configured server

2. **Server Registry** (`src/mcp_proxy_server/server_registry.py`)
   - Manages backend server lifecycle (start/stop/restart)
   - Handles health monitoring and process management
   - Prepares environment variables for backend processes

3. **Configuration System** (`src/mcp_proxy_server/config.py`)
   - Loads and validates proxy configuration
   - Handles environment variable expansion
   - Supports multiple deployment methods (uv, uvx, docker)

4. **Credential Management** (`src/mcp_proxy_server/credentials.py`)
   - Secure credential discovery and management
   - Environment variable substitution
   - Deployment-specific credential handling

5. **Session Management** (`src/mcp_proxy_server/session/`)
   - Session isolation for multiple clients
   - Lifecycle management for server instances

6. **Middleware** (`src/mcp_proxy_server/middleware/`)
   - Namespace routing to avoid tool/resource conflicts
   - Request transformation and validation

### Known Architectural Issues

**CRITICAL**: The current implementation has a dual execution path problem:
- Server Registry starts backend processes with proper environment variables
- ProxyClient creates separate subprocess instances WITHOUT environment variables
- Only the ProxyClient processes are used for client communication, causing failures

When working on the proxy server, be aware that environment variables need to be passed to ProxyClient instances in `src/mcp_proxy_server/proxy/server.py:_create_proxy_client()`.

### Configuration Structure

The proxy uses JSON configuration files with this structure:
```json
{
  "host": "localhost",
  "port": 8080,
  "transport": "stdio",
  "servers": [
    {
      "name": "server-name",
      "transport": "stdio",
      "command": ["command", "args"],
      "enabled": true,
      "timeout": 30,
      "namespace": "optional-namespace",
      "env": {
        "API_KEY": "${ENV_VAR_NAME}"
      }
    }
  ]
}
```

Environment variables are expanded using `${VAR_NAME}` syntax with optional defaults `${VAR_NAME:-default}`.

## Entry Points

- CLI: `src/mcp_proxy_server/cli.py` - Main command-line interface
- Package: `src/mcp_proxy_server/__main__.py` - Python module entry point
- Script: `mcp-proxy` - Installed console script (defined in pyproject.toml)

## Dependencies

- **FastMCP**: Core MCP protocol implementation and proxy capabilities
- **Pydantic**: Configuration validation and data models
- **Click**: Command-line interface framework
- **HTTPX**: HTTP client for server communication
- **Python-dotenv**: Environment variable loading from .env files

## Testing Strategy

Tests are organized by type:
- Unit tests: Fast, isolated component testing
- Integration tests: Cross-component interaction testing
- Slow tests: End-to-end scenarios with real subprocess execution

Coverage target is 80% minimum as defined in pyproject.toml.