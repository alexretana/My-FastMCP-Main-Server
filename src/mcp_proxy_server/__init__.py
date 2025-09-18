"""
MCP Proxy Server - A high-performance proxy for aggregating multiple MCP servers.

This package provides a production-ready proxy server that can aggregate multiple
Model Context Protocol (MCP) servers, providing unified access through a single
endpoint with support for multiple transport protocols.
"""

__version__ = "0.1.0"
__author__ = "MCP Proxy Contributors"
__email__ = "mcp-proxy@example.com"

from .proxy import MCPProxyServer
from .config import ProxyConfig, ServerConfig
from .credentials import CredentialManager

__all__ = [
    "MCPProxyServer",
    "ProxyConfig",
    "ServerConfig",
    "CredentialManager",
    "__version__",
]