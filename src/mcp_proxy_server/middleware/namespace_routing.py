"""
Namespace routing middleware for FastMCP proxy server.

Implements conflict resolution for duplicate names using namespace prefixing.
"""

import logging
from typing import Dict, Any, List, Optional

from fastmcp.server.middleware import Middleware, MiddlewareContext

from ..server_registry import ServerRegistry

logger = logging.getLogger(__name__)


class NamespaceRoutingMiddleware(Middleware):
    """
    Middleware for namespace-based tool and resource routing.

    Implements conflict resolution for duplicate names using namespace prefixing.
    """

    def __init__(self, server_registry: ServerRegistry):
        self.server_registry = server_registry
        self.namespace_map: Dict[str, str] = {}  # tool/resource name -> server name
        self.conflict_count: Dict[str, int] = {}  # track naming conflicts

    async def on_list_tools(self, context: MiddlewareContext, call_next):
        """Apply namespace prefixing to tools from different servers."""
        result = await call_next(context)

        # Track and resolve naming conflicts
        self._resolve_naming_conflicts(result, "tool")

        return result

    async def on_list_resources(self, context: MiddlewareContext, call_next):
        """Apply namespace prefixing to resources from different servers."""
        result = await call_next(context)

        # Track and resolve naming conflicts
        self._resolve_naming_conflicts(result, "resource")

        return result

    def _resolve_naming_conflicts(self, items: List[Any], item_type: str) -> None:
        """Resolve naming conflicts by applying namespace prefixes."""
        name_counts = {}

        # Count occurrences of each name
        for item in items:
            name = getattr(item, 'name', None)
            if name:
                name_counts[name] = name_counts.get(name, 0) + 1

        # Apply prefixes for conflicts
        for item in items:
            name = getattr(item, 'name', None)
            if name and name_counts[name] > 1:
                # Get server name from registry (simplified approach)
                server_name = self._get_server_for_item(item)
                if server_name:
                    prefixed_name = f"{server_name}:{name}"
                    setattr(item, 'name', prefixed_name)
                    self.namespace_map[prefixed_name] = server_name
                    self.conflict_count[name] = name_counts[name]

                    logger.debug(f"Applied namespace prefix: {name} -> {prefixed_name}")

    def _get_server_for_item(self, item: Any) -> Optional[str]:
        """Get the server name that provides this item (simplified)."""
        # In a real implementation, this would track which server provides each item
        # For now, return the first active server as a placeholder
        active_servers = self.server_registry.get_active_servers()
        return active_servers[0] if active_servers else None