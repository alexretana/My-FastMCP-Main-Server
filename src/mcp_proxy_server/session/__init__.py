"""
Session management components for FastMCP proxy server.

This module contains classes for managing client sessions, isolation,
and lifecycle in the proxy server.
"""

from .isolation_manager import SessionIsolationManager

__all__ = [
    "SessionIsolationManager",
]