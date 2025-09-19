"""
FastMCP-based proxy implementation with routing and session isolation.

This module implements the core proxy functionality using FastMCP's native
proxy capabilities for tool/resource routing and session management.

This is the main entry point that imports and exposes all refactored components.
"""

# Import all refactored components for backward compatibility
from .middleware import NamespaceRoutingMiddleware, RequestTransformationMiddleware
from .session import SessionIsolationManager
from .proxy import FastMCPProxyServer

# Re-export for backward compatibility
__all__ = [
    "NamespaceRoutingMiddleware",
    "RequestTransformationMiddleware",
    "SessionIsolationManager",
    "FastMCPProxyServer",
]