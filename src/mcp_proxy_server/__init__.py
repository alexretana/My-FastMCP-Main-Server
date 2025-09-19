"""
MCP Proxy Server - A high-performance proxy for aggregating multiple MCP servers.

This package provides a production-ready proxy server that can aggregate multiple
Model Context Protocol (MCP) servers, providing unified access through a single
endpoint with support for multiple transport protocols.
"""

__version__ = "0.1.0"
__author__ = "Alexander Retana (AI Generated)"
__email__ = "alex.retana@live.com"

from .proxy import FastMCPProxyServer
from .config import ProxyConfig, ServerConfig
from .credentials import CredentialManager

# Keep backward compatibility alias
MCPProxyServer = FastMCPProxyServer

__all__ = [
    "FastMCPProxyServer",
    "MCPProxyServer",  # backward compatibility
    "ProxyConfig",
    "ServerConfig",
    "CredentialManager",
    "__version__",
]