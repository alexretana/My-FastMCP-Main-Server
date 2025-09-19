"""
Routing components for FastMCP proxy server.

Handles tool and resource routing resolution and namespace prefix application.
"""

import logging
from typing import Dict, Any, Optional

from ..server_registry import ServerRegistry

logger = logging.getLogger(__name__)


class ProxyRouting:
    """
    Handles routing resolution for tools and resources in the proxy server.
    """

    def __init__(self, server_registry: ServerRegistry, proxy_clients: Dict[str, Any]):
        self.server_registry = server_registry
        self.proxy_clients = proxy_clients

    def resolve_tool_server(self, tool_name: str) -> Optional[str]:
        """Resolve which server should handle a tool call."""
        # Check if tool name has namespace prefix
        if ':' in tool_name:
            server_name, _ = tool_name.split(':', 1)
            if server_name in self.proxy_clients:
                return server_name

        # Default to first available server for now
        # In a real implementation, you'd maintain a tool registry
        active_servers = self.server_registry.get_active_servers()
        return active_servers[0] if active_servers else None

    def resolve_resource_server(self, uri: str) -> Optional[str]:
        """Resolve which server should handle a resource request."""
        # Similar to tool resolution but for resources
        # Could be based on URI scheme, path patterns, etc.
        active_servers = self.server_registry.get_active_servers()
        return active_servers[0] if active_servers else None

    def apply_namespace_prefix(self, item: Dict[str, Any], server_name: str) -> Dict[str, Any]:
        """Apply namespace prefix to an item if needed."""
        # This would check for naming conflicts and apply prefixes
        # For now, just return the item as-is
        return item