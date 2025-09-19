"""
Middleware components for FastMCP proxy server.

This module contains middleware classes that handle request/response processing,
routing, and transformations in the proxy pipeline.
"""

from .namespace_routing import NamespaceRoutingMiddleware
from .request_transformation import RequestTransformationMiddleware

__all__ = [
    "NamespaceRoutingMiddleware",
    "RequestTransformationMiddleware",
]