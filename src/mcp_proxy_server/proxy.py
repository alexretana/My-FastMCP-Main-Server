"""
Main MCP Proxy Server implementation.

This module provides the core proxy server functionality using FastMCP
for aggregating multiple MCP servers.
"""

import asyncio
import logging
import signal
import sys
from typing import Dict, Any, Optional
from contextlib import asynccontextmanager

from .config import ProxyConfig
from .server_registry import ServerRegistry
from .fastmcp_proxy import FastMCPProxyServer

logger = logging.getLogger(__name__)


class MCPProxyServer:
    """
    Main MCP Proxy Server that aggregates multiple backend MCP servers.

    This server acts as a proxy, routing requests to appropriate backend
    servers and aggregating responses using FastMCP capabilities.
    """

    def __init__(self, config: ProxyConfig, credentials: Dict[str, Any]):
        """Initialize the proxy server with configuration and credentials."""
        self.config = config
        self.credentials = credentials
        self.server_registry = ServerRegistry(config, credentials)
        self.running = False
        self._shutdown_event = asyncio.Event()

        # Initialize FastMCP-based proxy server
        self.fastmcp_proxy = FastMCPProxyServer(config, credentials)

    async def start(self) -> None:
        """Start the proxy server and all backend servers."""
        logger.info("Starting MCP Proxy Server...")

        try:
            # Initialize FastMCP proxy server
            await self.fastmcp_proxy.initialize()

            # Start FastMCP proxy server
            await self.fastmcp_proxy.start()

            self.running = True
            logger.info(f"MCP Proxy Server started on {self.config.host}:{self.config.port}")
            logger.info(f"Transport: {self.config.transport}")
            logger.info(f"Active servers: {len(self.server_registry.get_active_servers())}")

        except Exception as e:
            logger.error(f"Failed to start proxy server: {e}")
            raise

    async def stop(self) -> None:
        """Stop the proxy server and all backend servers."""
        logger.info("Stopping MCP Proxy Server...")

        self.running = False
        self._shutdown_event.set()

        try:
            # Stop FastMCP proxy server
            await self.fastmcp_proxy.stop()
            logger.info("All servers stopped")

        except Exception as e:
            logger.error(f"Error during shutdown: {e}")

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
                    await self.fastmcp_proxy.initialize()
                    await self.fastmcp_proxy.start()
                    await self.fastmcp_proxy.run_stdio_async()

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

    async def health_check(self) -> Dict[str, Any]:
        """Perform health check on the proxy and all backend servers."""
        health_status = {
            "status": "healthy" if self.running else "stopped",
            "proxy": {
                "running": self.running,
                "config": {
                    "host": self.config.host,
                    "port": self.config.port,
                    "transport": self.config.transport,
                }
            },
            "servers": {}
        }

        if self.running:
            # Check all backend servers
            for server_name, status in await self.server_registry.get_all_server_status():
                health_status["servers"][server_name] = status

            # Add FastMCP proxy statistics
            if hasattr(self, 'fastmcp_proxy'):
                proxy_stats = self.fastmcp_proxy.get_proxy_stats()
                health_status["fastmcp_proxy"] = proxy_stats

        return health_status

    async def get_server_info(self) -> Dict[str, Any]:
        """Get detailed information about the proxy and backend servers."""
        server_info = {
            "proxy": {
                "version": "0.1.0",  # TODO: Get from package
                "config": self.config.dict(),
                "running": self.running,
                "deployment_method": self.config.deployment_method,
            },
            "servers": await self.server_registry.get_server_details(),
            "credentials": {
                "sources": list(self.credentials.keys()),
                "secure": True,  # TODO: Check credential security
            }
        }

        # Add FastMCP proxy information
        if hasattr(self, 'fastmcp_proxy'):
            proxy_stats = self.fastmcp_proxy.get_proxy_stats()
            server_info["fastmcp_proxy"] = proxy_stats

        return server_info