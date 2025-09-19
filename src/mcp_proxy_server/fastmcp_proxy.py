"""
FastMCP-based proxy implementation with routing and session isolation.

This module implements the core proxy functionality using FastMCP's native
proxy capabilities for tool/resource routing and session management.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Set
from contextlib import asynccontextmanager

from fastmcp import FastMCP, Context
from fastmcp.server.proxy import ProxyClient, FastMCPProxy
from fastmcp.server.middleware import Middleware, MiddlewareContext
from fastmcp.exceptions import FastMCPError

from .config import ProxyConfig, ServerConfig, TransportType
from .server_registry import ServerRegistry, ServerStatus
from .session_lifecycle import SessionLifecycleManager, SessionEvent, FastMCPSessionContext

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


class RequestTransformationMiddleware(Middleware):
    """
    Middleware for request/response transformation pipeline.

    Handles request modification, validation, and response formatting.
    """

    def __init__(self, proxy_config: ProxyConfig):
        self.config = proxy_config
        self.transformation_rules: Dict[str, Any] = {}

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        """Transform tool requests before forwarding."""
        # Apply request transformations
        transformed_context = await self._transform_request(context)

        # Call the next middleware/handler
        result = await call_next(transformed_context)

        # Apply response transformations
        transformed_result = await self._transform_response(result, context)

        return transformed_result

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        """Transform resource requests before forwarding."""
        # Apply URI transformations if needed
        transformed_context = await self._transform_resource_uri(context)

        result = await call_next(transformed_context)

        # Apply content transformations
        transformed_result = await self._transform_resource_content(result, context)

        return transformed_result

    async def _transform_request(self, context: MiddlewareContext) -> MiddlewareContext:
        """Apply request transformations."""
        # Extract tool name and arguments
        request_data = getattr(context, 'request_data', {})
        tool_name = request_data.get('name', '')
        arguments = request_data.get('arguments', {})

        # Apply transformation rules
        if tool_name in self.transformation_rules:
            rules = self.transformation_rules[tool_name]

            # Transform arguments
            if 'argument_mapping' in rules:
                for old_arg, new_arg in rules['argument_mapping'].items():
                    if old_arg in arguments:
                        arguments[new_arg] = arguments.pop(old_arg)

            # Add default arguments
            if 'default_arguments' in rules:
                for arg_name, default_value in rules['default_arguments'].items():
                    if arg_name not in arguments:
                        arguments[arg_name] = default_value

        return context

    async def _transform_response(self, result: Any, context: MiddlewareContext) -> Any:
        """Apply response transformations."""
        # Add response metadata, filtering, etc.
        # For now, just pass through
        return result

    async def _transform_resource_uri(self, context: MiddlewareContext) -> MiddlewareContext:
        """Transform resource URIs for routing."""
        # Apply URI rewriting rules
        return context

    async def _transform_resource_content(self, result: Any, context: MiddlewareContext) -> Any:
        """Transform resource content."""
        # Apply content filtering, formatting, etc.
        return result


class SessionIsolationManager:
    """
    Manages client session isolation using FastMCP session handling.

    Ensures each client gets isolated backend sessions and proper cleanup.
    """

    def __init__(self, server_registry: ServerRegistry):
        self.server_registry = server_registry
        self.client_sessions: Dict[str, Dict[str, Any]] = {}  # client_id -> session_data
        self.backend_clients: Dict[str, Dict[str, ProxyClient]] = {}  # client_id -> server_name -> client
        self.session_locks: Dict[str, asyncio.Lock] = {}  # Per-client locks

        # Initialize session lifecycle management
        self.lifecycle_manager = SessionLifecycleManager()
        self.fastmcp_session_context = FastMCPSessionContext(self.lifecycle_manager)

    async def get_or_create_client_session(self, client_id: str) -> Dict[str, Any]:
        """Get or create a session for a client."""
        if client_id not in self.client_sessions:
            await self._create_client_session(client_id)

        return self.client_sessions[client_id]

    async def _create_client_session(self, client_id: str) -> None:
        """Create a new isolated session for a client."""
        logger.info(f"Creating new session for client: {client_id}")

        # Create session lock
        self.session_locks[client_id] = asyncio.Lock()

        # Initialize session data
        session_data = {
            'created_at': asyncio.get_event_loop().time(),
            'last_activity': asyncio.get_event_loop().time(),
            'request_count': 0,
            'active_requests': 0,
        }
        self.client_sessions[client_id] = session_data

        # Emit session lifecycle event
        client_info = {'client_id': client_id, 'user_agent': 'mcp-proxy-client'}
        await self.lifecycle_manager.create_session_event(
            client_id,
            SessionEvent.CREATED,
            client_info=client_info
        )

        # Create isolated backend clients for each server
        self.backend_clients[client_id] = {}

        for server_name in self.server_registry.get_active_servers():
            backend_client = await self._create_backend_client(server_name)
            if backend_client:
                self.backend_clients[client_id][server_name] = backend_client
                logger.debug(f"Created backend client for {server_name} in session {client_id}")

        # Emit session started event
        await self.lifecycle_manager.create_session_event(
            client_id,
            SessionEvent.STARTED
        )

    async def _create_backend_client(self, server_name: str) -> Optional[ProxyClient]:
        """Create a backend client for a specific server."""
        server_status = await self.server_registry.get_server_status(server_name)
        if not server_status or server_status['status'] != ServerStatus.RUNNING:
            logger.warning(f"Server {server_name} not available for backend client creation")
            return None

        server_config = server_status['config']

        try:
            if server_config['transport'] == TransportType.STDIO:
                # For stdio, create a fresh ProxyClient that will manage the process
                return ProxyClient(server_config['command'])
            elif server_config['transport'] == TransportType.HTTP:
                # For HTTP, create ProxyClient with URL
                return ProxyClient(server_config['url'])
            elif server_config['transport'] == TransportType.SSE:
                # For SSE, create ProxyClient with SSE URL
                return ProxyClient(server_config['url'])
            else:
                logger.error(f"Unsupported transport for backend client: {server_config['transport']}")
                return None

        except Exception as e:
            logger.error(f"Failed to create backend client for {server_name}: {e}")
            return None

    async def get_backend_client(self, client_id: str, server_name: str) -> Optional[ProxyClient]:
        """Get the backend client for a specific server in a client session."""
        if client_id not in self.backend_clients:
            await self.get_or_create_client_session(client_id)

        return self.backend_clients.get(client_id, {}).get(server_name)

    async def update_session_activity(self, client_id: str, request_data: Optional[Dict[str, Any]] = None) -> None:
        """Update last activity timestamp for a session."""
        if client_id in self.client_sessions:
            self.client_sessions[client_id]['last_activity'] = asyncio.get_event_loop().time()
            self.client_sessions[client_id]['request_count'] += 1

            # Emit request received event
            await self.lifecycle_manager.create_session_event(
                client_id,
                SessionEvent.REQUEST_RECEIVED,
                request_data=request_data
            )

    async def cleanup_session(self, client_id: str) -> None:
        """Clean up a client session and all associated resources."""
        logger.info(f"Cleaning up session for client: {client_id}")

        # Emit cleanup started event
        await self.lifecycle_manager.create_session_event(
            client_id,
            SessionEvent.CLEANUP_STARTED
        )

        try:
            # Clean up backend clients
            if client_id in self.backend_clients:
                for server_name, client in self.backend_clients[client_id].items():
                    try:
                        if hasattr(client, 'close'):
                            await client.close()
                    except Exception as e:
                        logger.error(f"Error closing backend client for {server_name}: {e}")
                        # Emit error event
                        await self.lifecycle_manager.create_session_event(
                            client_id,
                            SessionEvent.ERROR_OCCURRED,
                            error_info={'type': 'cleanup_error', 'server': server_name, 'message': str(e)}
                        )

                del self.backend_clients[client_id]

            # Clean up session data
            if client_id in self.client_sessions:
                session_data = self.client_sessions[client_id]
                duration = asyncio.get_event_loop().time() - session_data['created_at']
                del self.client_sessions[client_id]

            # Clean up session lock
            if client_id in self.session_locks:
                del self.session_locks[client_id]

            logger.debug(f"Session cleanup completed for client: {client_id}")

            # Emit session destroyed event
            await self.lifecycle_manager.create_session_event(
                client_id,
                SessionEvent.DESTROYED,
                metrics={'session_duration': duration, 'total_requests': session_data.get('request_count', 0)}
            )

        except Exception as e:
            logger.error(f"Error during session cleanup for {client_id}: {e}")
            await self.lifecycle_manager.create_session_event(
                client_id,
                SessionEvent.ERROR_OCCURRED,
                error_info={'type': 'cleanup_error', 'message': str(e)}
            )

    async def cleanup_idle_sessions(self, max_idle_time: float = 3600) -> None:
        """Clean up sessions that have been idle for too long."""
        current_time = asyncio.get_event_loop().time()
        idle_clients = []

        for client_id, session_data in self.client_sessions.items():
            if current_time - session_data['last_activity'] > max_idle_time:
                idle_clients.append(client_id)

        for client_id in idle_clients:
            logger.info(f"Cleaning up idle session: {client_id}")

            # Emit idle timeout event
            await self.lifecycle_manager.create_session_event(
                client_id,
                SessionEvent.IDLE_TIMEOUT,
                metrics={'idle_time': current_time - self.client_sessions[client_id]['last_activity']}
            )

            await self.cleanup_session(client_id)

    def get_session_stats(self) -> Dict[str, Any]:
        """Get statistics about active sessions."""
        basic_stats = {
            'active_sessions': len(self.client_sessions),
            'total_backend_clients': sum(len(clients) for clients in self.backend_clients.values()),
            'session_details': {
                client_id: {
                    'created_at': session_data['created_at'],
                    'last_activity': session_data['last_activity'],
                    'request_count': session_data['request_count'],
                    'backend_clients': list(self.backend_clients.get(client_id, {}).keys())
                }
                for client_id, session_data in self.client_sessions.items()
            }
        }

        # Add lifecycle management stats
        lifecycle_stats = self.lifecycle_manager.get_lifecycle_stats()
        basic_stats['lifecycle'] = lifecycle_stats

        return basic_stats


class FastMCPProxyServer:
    """
    FastMCP-based proxy server with advanced routing and session isolation.

    Implements the core proxy functionality using FastMCP's native capabilities.
    """

    def __init__(self, config: ProxyConfig, credentials: Dict[str, Any]):
        self.config = config
        self.credentials = credentials
        self.server_registry = ServerRegistry(config, credentials)

        # Initialize FastMCP components
        self.fastmcp_server: Optional[FastMCP] = None
        self.session_manager = SessionIsolationManager(self.server_registry)

        # Middleware components
        self.routing_middleware = NamespaceRoutingMiddleware(self.server_registry)
        self.transformation_middleware = RequestTransformationMiddleware(config)

        # Proxy components
        self.proxy_clients: Dict[str, ProxyClient] = {}
        self.composite_proxy: Optional[FastMCPProxy] = None

        # Lifecycle management
        self.running = False
        self._cleanup_task: Optional[asyncio.Task] = None

    async def initialize(self) -> None:
        """Initialize the FastMCP proxy server."""
        logger.info("Initializing FastMCP proxy server...")

        # Initialize server registry
        await self.server_registry.initialize()

        # Create main FastMCP server instance
        proxy_name = getattr(self.config, 'name', 'MCP-Proxy')
        self.fastmcp_server = FastMCP(
            name=proxy_name,
            description="FastMCP-based proxy server for aggregating multiple MCP servers"
        )

        # Add middleware
        self.fastmcp_server.add_middleware(self.routing_middleware)
        self.fastmcp_server.add_middleware(self.transformation_middleware)

        # Create proxy clients for all active servers
        await self._create_proxy_clients()

        # Create composite proxy
        await self._create_composite_proxy()

        logger.info("FastMCP proxy server initialized")

    async def _create_proxy_clients(self) -> None:
        """Create ProxyClient instances for all configured servers."""
        for server_config in self.config.servers:
            if server_config.enabled:
                try:
                    proxy_client = await self._create_proxy_client(server_config)
                    if proxy_client:
                        self.proxy_clients[server_config.name] = proxy_client
                        logger.info(f"Created proxy client for server: {server_config.name}")
                except Exception as e:
                    logger.error(f"Failed to create proxy client for {server_config.name}: {e}")

    async def _create_proxy_client(self, server_config: ServerConfig) -> Optional[ProxyClient]:
        """Create a ProxyClient for a specific server configuration."""
        if server_config.transport == TransportType.STDIO:
            if not server_config.command:
                raise ValueError(f"stdio transport requires command for server {server_config.name}")
            return ProxyClient(server_config.command)

        elif server_config.transport == TransportType.HTTP:
            if not server_config.url:
                raise ValueError(f"HTTP transport requires URL for server {server_config.name}")
            return ProxyClient(server_config.url)

        elif server_config.transport == TransportType.SSE:
            if not server_config.url:
                raise ValueError(f"SSE transport requires URL for server {server_config.name}")
            return ProxyClient(server_config.url)

        else:
            raise ValueError(f"Unsupported transport type: {server_config.transport}")

    async def _create_composite_proxy(self) -> None:
        """Create a composite proxy that aggregates all backend servers."""
        if not self.proxy_clients:
            logger.warning("No proxy clients available for composite proxy")
            return

        # Create a factory function for fresh sessions
        def create_client_factory():
            # Return a function that creates new isolated clients per request
            async def client_factory():
                # This would create fresh, isolated connections per request
                # For now, reuse existing clients but this should be enhanced
                return list(self.proxy_clients.values())[0] if self.proxy_clients else None
            return client_factory

        # Create the composite proxy using FastMCP's as_proxy method
        proxy_name = getattr(self.config, 'name', 'MCP-Proxy')
        if len(self.proxy_clients) == 1:
            # Single backend server
            client = list(self.proxy_clients.values())[0]
            self.composite_proxy = FastMCP.as_proxy(client, name=f"{proxy_name}-Proxy")
        else:
            # Multiple backend servers - create a multi-server config
            config_dict = {
                "mcpServers": {
                    name: {
                        "url": client._connection_string if hasattr(client, '_connection_string') else "stdio",
                        "transport": "http" if "http" in str(client) else "stdio"
                    }
                    for name, client in self.proxy_clients.items()
                }
            }
            self.composite_proxy = FastMCP.as_proxy(config_dict, name=f"{proxy_name}-Composite-Proxy")

        logger.info(f"Created composite proxy with {len(self.proxy_clients)} backend servers")

    async def start(self) -> None:
        """Start the FastMCP proxy server."""
        logger.info("Starting FastMCP proxy server...")

        # Start server registry
        await self.server_registry.start_all_servers()

        # Start session cleanup task
        self._cleanup_task = asyncio.create_task(self._session_cleanup_loop())

        self.running = True
        logger.info("FastMCP proxy server started successfully")

    async def stop(self) -> None:
        """Stop the FastMCP proxy server."""
        logger.info("Stopping FastMCP proxy server...")

        self.running = False

        # Stop cleanup task
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # Clean up all sessions
        for client_id in list(self.session_manager.client_sessions.keys()):
            await self.session_manager.cleanup_session(client_id)

        # Close proxy clients
        for name, client in self.proxy_clients.items():
            try:
                if hasattr(client, 'close'):
                    await client.close()
            except Exception as e:
                logger.error(f"Error closing proxy client {name}: {e}")

        # Stop server registry
        await self.server_registry.stop_all_servers()

        logger.info("FastMCP proxy server stopped")

    async def _session_cleanup_loop(self) -> None:
        """Background task for cleaning up idle sessions."""
        while self.running:
            try:
                await self.session_manager.cleanup_idle_sessions()
                await asyncio.sleep(300)  # Check every 5 minutes
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in session cleanup loop: {e}")
                await asyncio.sleep(60)  # Wait 1 minute before retrying

    async def handle_request(self, client_id: str, request_type: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle a request from a client with session isolation."""
        # Ensure client has a session
        await self.session_manager.get_or_create_client_session(client_id)

        # Update session activity
        await self.session_manager.update_session_activity(client_id, request_data)

        try:
            # Route the request through the composite proxy
            if self.composite_proxy:
                # Use FastMCP's built-in routing and handling
                if request_type == "tools/call":
                    result = await self._handle_tool_call(client_id, request_data)
                elif request_type == "resources/read":
                    result = await self._handle_resource_read(client_id, request_data)
                elif request_type == "tools/list":
                    result = await self._handle_tools_list(client_id, request_data)
                elif request_type == "resources/list":
                    result = await self._handle_resources_list(client_id, request_data)
                else:
                    raise FastMCPError(f"Unsupported request type: {request_type}")

                return result
            else:
                raise FastMCPError("Composite proxy not available")

        except Exception as e:
            logger.error(f"Error handling request for client {client_id}: {e}")

            # Emit error event
            await self.session_manager.lifecycle_manager.create_session_event(
                client_id,
                SessionEvent.ERROR_OCCURRED,
                error_info={'type': 'request_error', 'message': str(e), 'request_type': request_type}
            )
            raise

    async def _handle_tool_call(self, client_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tool call with session isolation."""
        tool_name = request_data.get('name', '')
        arguments = request_data.get('arguments', {})

        # Determine which server should handle this tool
        server_name = self._resolve_tool_server(tool_name)
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

    async def _handle_resource_read(self, client_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resource read with session isolation."""
        uri = request_data.get('uri', '')

        # Determine which server should handle this resource
        server_name = self._resolve_resource_server(uri)
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

    async def _handle_tools_list(self, client_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle tools list with namespace resolution."""
        # This would aggregate tools from all servers with namespace prefixing
        tools = []

        for server_name in self.server_registry.get_active_servers():
            backend_client = await self.session_manager.get_backend_client(client_id, server_name)
            if backend_client:
                # Get tools from this server and apply namespace prefixes
                server_tools = []  # Would call backend_client.list_tools()

                for tool in server_tools:
                    # Apply namespace prefixing if there are conflicts
                    prefixed_tool = self._apply_namespace_prefix(tool, server_name)
                    tools.append(prefixed_tool)

        return {"tools": tools}

    async def _handle_resources_list(self, client_id: str, request_data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle resources list with namespace resolution."""
        # Similar to tools list but for resources
        resources = []

        for server_name in self.server_registry.get_active_servers():
            backend_client = await self.session_manager.get_backend_client(client_id, server_name)
            if backend_client:
                # Get resources from this server and apply namespace prefixes
                server_resources = []  # Would call backend_client.list_resources()

                for resource in server_resources:
                    # Apply namespace prefixing if there are conflicts
                    prefixed_resource = self._apply_namespace_prefix(resource, server_name)
                    resources.append(prefixed_resource)

        return {"resources": resources}

    def _resolve_tool_server(self, tool_name: str) -> Optional[str]:
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

    def _resolve_resource_server(self, uri: str) -> Optional[str]:
        """Resolve which server should handle a resource request."""
        # Similar to tool resolution but for resources
        # Could be based on URI scheme, path patterns, etc.
        active_servers = self.server_registry.get_active_servers()
        return active_servers[0] if active_servers else None

    def _apply_namespace_prefix(self, item: Dict[str, Any], server_name: str) -> Dict[str, Any]:
        """Apply namespace prefix to an item if needed."""
        # This would check for naming conflicts and apply prefixes
        # For now, just return the item as-is
        return item

    def get_proxy_stats(self) -> Dict[str, Any]:
        """Get statistics about the proxy server."""
        return {
            "proxy": {
                "running": self.running,
                "config": self.config.dict(),
                "backend_servers": len(self.proxy_clients),
                "active_servers": len(self.server_registry.get_active_servers())
            },
            "session_management": self.session_manager.get_session_stats(),
            "routing": {
                "namespace_conflicts": len(self.routing_middleware.conflict_count),
                "namespace_mappings": len(self.routing_middleware.namespace_map)
            }
        }

    def run_stdio(self) -> None:
        """Run the proxy using stdio transport (for CLI integration)."""
        if self.composite_proxy:
            # Run the composite proxy with stdio transport
            self.composite_proxy.run()  # Defaults to stdio
        else:
            logger.error("Composite proxy not available")
            raise RuntimeError("Composite proxy not initialized")

    async def run_http(self, host: str = "0.0.0.0", port: int = 8000) -> None:
        """Run the proxy using HTTP transport."""
        if self.composite_proxy:
            # Run the composite proxy with HTTP transport
            await self.composite_proxy.run_async(transport="http", host=host, port=port)
        else:
            logger.error("Composite proxy not available")
            raise RuntimeError("Composite proxy not initialized")