# MCP Proxy Server

A high-performance proxy server for aggregating multiple MCP (Model Context Protocol) servers, built with FastMCP. This proxy allows you to combine multiple MCP servers into a single endpoint, providing unified access to all their tools and resources.

## Features

- **Multi-Server Aggregation**: Combine multiple MCP servers into one endpoint
- **Multiple Transport Support**: stdio, HTTP, and SSE protocols
- **Flexible Deployment**: Works with `uv install`, `uvx run`, and Docker
- **Credential Management**: Secure credential discovery and environment variable support
- **Health Monitoring**: Built-in health checks and server monitoring
- **Namespace Support**: Avoid tool/resource name conflicts with namespacing
- **Configuration Validation**: Comprehensive config validation with helpful error messages

## Quick Start

### Option 1: Direct execution with uvx (Recommended for testing)

```bash
# Create a config file
uvx run mcp-proxy-server create-config --deployment uvx

# Edit the generated mcp-proxy.json file with your server configurations

# Run the proxy
uvx run mcp-proxy-server run --config mcp-proxy.json
```

### Option 2: Install with uv (Recommended for development)

```bash
# Install the package
uv add mcp-proxy-server

# Create configuration
mcp-proxy create-config --deployment uv --with-credentials

# Run the proxy
mcp-proxy run --config mcp-proxy.json
```

### Option 3: Docker deployment (Recommended for production)

```bash
# Copy templates
cp templates/configs/docker.json config/
cp templates/.env.template .env

# Edit configuration and environment files
# Then run with docker-compose
docker-compose -f templates/docker-compose.yml up
```

## Configuration

### Basic Configuration Example

```json
{
  "host": "localhost",
  "port": 8080,
  "transport": "stdio",
  "log_level": "INFO",
  "servers": [
    {
      "name": "playwright",
      "transport": "stdio",
      "command": ["npx", "@playwright/mcp@latest"],
      "enabled": false,
      "timeout": 30,
      "namespace": "playwright"
    },
    {
      "name": "obsidian",
      "transport": "stdio",
      "command": ["uvx", "mcp-obsidian"],
      "enabled": true,
      "timeout": 30,
      "namespace": "obsidian",
      "env": {
        "OBSIDIAN_API_KEY": "${OBSIDIAN_API_KEY}",
        "OBSIDIAN_HOST": "${OBSIDIAN_HOST:-127.0.0.1}",
        "OBSIDIAN_PORT": "${OBSIDIAN_PORT:-27124}"
      }
    }
  ]
}
```

### Client Configuration

#### Claude Desktop

Add to your Claude Desktop configuration:

```json
{
  "mcpServers": {
    "mcp-proxy": {
      "command": "mcp-proxy",
      "args": ["run", "--config", "/path/to/mcp-proxy.json"]
    }
  }
}
```

**Alternative Configuration (using working directory):**

If you prefer to use a working directory approach without specifying the config path explicitly:

```json
{
  "mcpServers": {
    "fastmcp-main-server": {
      "type": "stdio",
      "command": "D:\\path\\to\\mcp-proxy.exe",
      "args": ["run"],
      "env": {
        "DEBUG": "true"
      },
      "cwd": "D:\\path\\to\\your\\project\\"
    }
  }
}
```

In this configuration:
- The `cwd` (current working directory) should point to the directory containing your config file
- The CLI will automatically look for `mcp-proxy.json`, `config.json`, or similar files in that directory
- No explicit `--config` argument is needed

#### Cursor

Add to your Cursor MCP settings:

```json
{
  "servers": {
    "mcp-proxy": {
      "command": "mcp-proxy",
      "args": ["run", "--config", "/path/to/mcp-proxy.json"]
    }
  }
}
```

## Commands

```bash
# Run the proxy server
mcp-proxy run --config config.json

# Validate configuration
mcp-proxy validate --config config.json --check-credentials

# Create configuration template
mcp-proxy create-config --output my-config.json --deployment uv

# Show server status
mcp-proxy status

# Show version information
mcp-proxy version
```

## Environment Variables

The proxy supports environment variable substitution in configuration files:

```bash
export MCP_PROXY_JWT_SECRET="your-secret"
export WEATHER_API_KEY="your-api-key"
export MCP_FILESYSTEM_ROOT="/allowed/directory"
```

## Credential Management

Credentials are discovered in the following order (highest to lowest priority):

1. Environment variables (`MCP_PROXY_*`)
2. Deployment-specific credential files
3. Default credential locations (`~/.config/mcp-proxy/credentials.json`)
4. Built-in defaults

## Development

```bash
# Clone the repository
git clone <repository-url>
cd mcp-proxy-server

# Install in development mode
uv pip install -e .

# Run tests
pytest

# Format code
black src/
isort src/

# Type checking
mypy src/
```

## Deployment Methods

### UV Install Deployment
- Install via `uv add mcp-proxy-server`
- System-wide configuration in `~/.config/mcp-proxy/`
- Best for development and personal use

### UVX Run Deployment
- Direct execution via `uvx run mcp-proxy-server`
- Portable configuration in current directory
- Best for testing and one-off usage

### Docker Deployment
- Containerized deployment with docker-compose
- Production-ready with health checks and monitoring
- Best for production environments

## License

MIT License - see LICENSE file for details.
