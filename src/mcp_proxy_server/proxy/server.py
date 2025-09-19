"""
Core FastMCP proxy server implementation.

FastMCP-based proxy server with advanced routing and session isolation.
"""

import asyncio
import logging
import signal
import sys
from typing import Dict, Any, Optional

from fastmcp import FastMCP
from fastmcp.server.proxy import ProxyClient, FastMCPProxy
from fastmcp.exceptions import FastMCPError

from ..config import ProxyConfig, ServerConfig, TransportType
from ..server_registry import ServerRegistry
from ..session.isolation_manager import SessionIsolationManager
from ..session_lifecycle import SessionEvent
from ..middleware.namespace_routing import NamespaceRoutingMiddleware
from ..middleware.request_transformation import RequestTransformationMiddleware
from .request_handlers import ProxyRequestHandlers
from .routing import ProxyRouting

logger = logging.getLogger(__name__)


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

        # Proxy components
        self.proxy_clients: Dict[str, ProxyClient] = {}
        self.composite_proxy: Optional[FastMCPProxy] = None

        # Initialize routing and request handling
        self.routing = ProxyRouting(self.server_registry, self.proxy_clients)
        self.request_handlers = ProxyRequestHandlers(self.session_manager, self.routing)

        # Middleware components
        self.routing_middleware = NamespaceRoutingMiddleware(self.server_registry)
        self.transformation_middleware = RequestTransformationMiddleware(config)

        # Lifecycle management
        self.running = False
        self._cleanup_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Initialize the FastMCP proxy server."""
        logger.info("Initializing FastMCP proxy server...")

        # Initialize server registry
        await self.server_registry.initialize()

        # Create main FastMCP server instance
        proxy_name = getattr(self.config, 'name', 'MCP-Proxy')
        self.fastmcp_server = FastMCP(
            name=proxy_name,
            instructions="FastMCP-based proxy server for aggregating multiple MCP servers"
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
            # FastMCP ProxyClient requires either a string command or MCPConfig dict
            command = server_config.command
            if isinstance(command, list):
                # Convert command list to MCPConfig format
                mcp_config = {
                    'mcpServers': {
                        server_config.name: {
                            'command': ' '.join(command),
                            'transport': 'stdio'
                        }
                    }
                }
                return ProxyClient(mcp_config)
            else:
                # Command is already a string
                mcp_config = {
                    'mcpServers': {
                        server_config.name: {
                            'command': command,
                            'transport': 'stdio'
                        }
                    }
                }
                return ProxyClient(mcp_config)

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
            # Route the request through the appropriate handler
            if request_type == "tools/call":
                result = await self.request_handlers.handle_tool_call(client_id, request_data)
            elif request_type == "resources/read":
                result = await self.request_handlers.handle_resource_read(client_id, request_data)
            elif request_type == "tools/list":
                result = await self.request_handlers.handle_tools_list(client_id, request_data)
            elif request_type == "resources/list":
                result = await self.request_handlers.handle_resources_list(client_id, request_data)
            else:
                raise FastMCPError(f"Unsupported request type: {request_type}")

            return result

        except Exception as e:
            logger.error(f"Error handling request for client {client_id}: {e}")

            # Emit error event
            await self.session_manager.lifecycle_manager.create_session_event(
                client_id,
                SessionEvent.ERROR_OCCURRED,
                error_info={'type': 'request_error', 'message': str(e), 'request_type': request_type}
            )
            raise

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

    async def run_stdio_async(self) -> None:
        """Run the proxy using stdio transport in async context."""
        if self.composite_proxy:
            # Run the composite proxy with stdio transport asynchronously
            await self.composite_proxy.run_async(transport="stdio")
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

    # Backward compatibility methods
    async def run_async(self) -> None:
        """Run the proxy server asynchronously."""
        try:
            await self.start()

            # Wait for shutdown signal
            await self._shutdown_event.wait()

        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")
        except Exception as e:
            logger.error(f"Server error: {e}")
            raise
        finally:
            await self.stop()

    def run(self) -> None:
        """Run the proxy server (blocking)."""
        # Set up signal handlers
        def signal_handler(signum, frame):
            logger.info(f"Received signal {signum}")
            self._shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        try:
            # For stdio transport, use FastMCP's direct running
            if self.config.transport.lower() == 'stdio':
                # Initialize the FastMCP proxy first
                async def init_and_run():
                    await self.initialize()
                    await self.start()
                    await self.run_stdio_async()

                asyncio.run(init_and_run())
            else:
                # Run the async server for HTTP/SSE
                asyncio.run(self.run_async())
        except KeyboardInterrupt:
            logger.info("Server interrupted")
        except Exception as e:
            logger.error(f"Server failed: {e}")
            sys.exit(1)

    def run_daemon(self) -> None:
        """Run the proxy server in daemon mode."""
        # TODO: Implement proper daemon mode with pid file, etc.
        logger.info("Daemon mode not yet implemented, running normally")
        self.run()