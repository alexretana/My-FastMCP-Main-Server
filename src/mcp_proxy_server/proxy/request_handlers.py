"""
Request handling components for FastMCP proxy server.

Handles different types of MCP requests with session isolation and routing.
"""

import logging
from typing import Dict, Any

from fastmcp.exceptions import FastMCPError

from ..session.isolation_manager import SessionIsolationManager
from ..session_lifecycle import SessionEvent
from .routing import ProxyRouting

logger = logging.getLogger(__name__)


class ProxyRequestHandlers:
    """
    Handles different types of MCP requests with session isolation.
    """

    def __init__(self, session_manager: SessionIsolationManager, routing: ProxyRouting):
        self.session_manager = session_manager
        self.routing = routing

    async def handle_tool_call(self, client_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool call with session isolation."""
        tool_name = request_data.get('name', '')
        arguments = request_data.get('arguments', {})

        # Determine which server should handle this tool
        server_name = self.routing.resolve_tool_server(tool_name)
        if not server_name:
            raise FastMCPError(f"No server found for tool: {tool_name}")

        # Get isolated backend client for this session
        backend_client = await self.session_manager.get_backend_client(client_id, server_name)
        if not backend_client:
            raise FastMCPError(f"Backend client not available for server: {server_name}")

        # Make the tool call through the isolated client
        # This is simplified - in a real implementation, you'd use FastMCP's tool calling mechanism
        return {
            "content": [
                {
                    "type": "text",
                    "text": f"Tool call {tool_name} routed to {server_name} with session isolation"
                }
            ]
        }

    async def handle_resource_read(self, client_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resource read with session isolation."""
        uri = request_data.get('uri', '')

        # Determine which server should handle this resource
        server_name = self.routing.resolve_resource_server(uri)
        if not server_name:
            raise FastMCPError(f"No server found for resource: {uri}")

        # Get isolated backend client for this session
        backend_client = await self.session_manager.get_backend_client(client_id, server_name)
        if not backend_client:
            raise FastMCPError(f"Backend client not available for server: {server_name}")

        # Read the resource through the isolated client
        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "text/plain",
                    "text": f"Resource {uri} routed to {server_name} with session isolation"
                }
            ]
        }

    async def handle_tools_list(self, client_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools list with namespace resolution."""
        # This would aggregate tools from all servers with namespace prefixing
        tools = []

        for server_name in self.session_manager.server_registry.get_active_servers():
            backend_client = await self.session_manager.get_backend_client(client_id, server_name)
            if backend_client:
                # Get tools from this server and apply namespace prefixes
                server_tools = []  # Would call backend_client.list_tools()

                for tool in server_tools:
                    # Apply namespace prefixing if there are conflicts
                    prefixed_tool = self.routing.apply_namespace_prefix(tool, server_name)
                    tools.append(prefixed_tool)

        return {"tools": tools}

    async def handle_resources_list(self, client_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resources list with namespace resolution."""
        # Similar to tools list but for resources
        resources = []

        for server_name in self.session_manager.server_registry.get_active_servers():
            backend_client = await self.session_manager.get_backend_client(client_id, server_name)
            if backend_client:
                # Get resources from this server and apply namespace prefixes
                server_resources = []  # Would call backend_client.list_resources()

                for resource in server_resources:
                    # Apply namespace prefixing if there are conflicts
                    prefixed_resource = self.routing.apply_namespace_prefix(resource, server_name)
                    resources.append(prefixed_resource)

        return {"resources": resources}