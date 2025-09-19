"""
Server registry for managing backend MCP servers.

This module handles the lifecycle, monitoring, and management of
backend MCP servers that the proxy aggregates.
"""

import asyncio
import logging
from typing import Dict, List, Tuple, Any, Optional
from dataclasses import dataclass, field
from enum import Enum
import time

from .config import ProxyConfig, ServerConfig, TransportType

logger = logging.getLogger(__name__)


class ServerStatus(str, Enum):
    """Server status enumeration."""
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    ERROR = "error"
    RECONNECTING = "reconnecting"


@dataclass
class ServerState:
    """Represents the state of a backend MCP server."""
    config: ServerConfig
    status: ServerStatus = ServerStatus.STOPPED
    last_started: Optional[float] = None
    last_error: Optional[str] = None
    error_count: int = 0
    restart_count: int = 0
    process: Optional[asyncio.subprocess.Process] = None
    connection_info: Dict[str, Any] = field(default_factory=dict)


class ServerRegistry:
    """
    Registry for managing multiple backend MCP servers.

    Handles server lifecycle, health monitoring, and connection management
    for all configured backend servers.
    """

    def __init__(self, config: ProxyConfig, credentials: Dict[str, Any]):
        """Initialize the server registry."""
        self.config = config
        self.credentials = credentials
        self.servers: Dict[str, ServerState] = {}
        self._monitor_task: Optional[asyncio.Task] = None
        self._shutdown_event = asyncio.Event()

    async def initialize(self) -> None:
        """Initialize the server registry and create server states."""
        logger.info("Initializing server registry...")

        # Create server states for all configured servers
        for server_config in self.config.servers:
            if server_config.enabled:
                self.servers[server_config.name] = ServerState(config=server_config)
                logger.info(f"Registered server: {server_config.name}")

        # Start monitoring task
        self._monitor_task = asyncio.create_task(self._monitor_servers())
        logger.info(f"Server registry initialized with {len(self.servers)} servers")

    async def start_all_servers(self) -> None:
        """Start all registered servers."""
        logger.info("Starting all servers...")

        start_tasks = []
        for server_name in self.servers:
            task = asyncio.create_task(self.start_server(server_name))
            start_tasks.append(task)

        # Wait for all servers to start (or fail)
        results = await asyncio.gather(*start_tasks, return_exceptions=True)

        successful = 0
        failed = 0
        for i, result in enumerate(results):
            server_name = list(self.servers.keys())[i]
            if isinstance(result, Exception):
                logger.error(f"Failed to start server {server_name}: {result}")
                failed += 1
            else:
                successful += 1

        logger.info(f"Server startup complete: {successful} successful, {failed} failed")

    async def start_server(self, server_name: str) -> bool:
        """Start a specific server."""
        if server_name not in self.servers:
            raise ValueError(f"Server '{server_name}' not found")

        server_state = self.servers[server_name]
        server_config = server_state.config

        logger.info(f"Starting server: {server_name}")
        server_state.status = ServerStatus.STARTING
        server_state.last_started = time.time()

        try:
            if server_config.transport == TransportType.STDIO:
                await self._start_stdio_server(server_state)
            elif server_config.transport == TransportType.HTTP:
                await self._start_http_server(server_state)
            elif server_config.transport == TransportType.SSE:
                await self._start_sse_server(server_state)
            else:
                raise ValueError(f"Unsupported transport: {server_config.transport}")

            server_state.status = ServerStatus.RUNNING
            server_state.error_count = 0
            logger.info(f"Server {server_name} started successfully")
            return True

        except Exception as e:
            server_state.status = ServerStatus.ERROR
            server_state.last_error = str(e)
            server_state.error_count += 1
            logger.error(f"Failed to start server {server_name}: {e}")
            return False

    async def _start_stdio_server(self, server_state: ServerState) -> None:
        """Start a stdio-based MCP server."""
        config = server_state.config

        if not config.command:
            raise ValueError("stdio transport requires a command")

        # Prepare environment - ensure all values are strings
        env = {k: str(v) for k, v in self.credentials.items() if isinstance(k, str)}
        env.update({k: str(v) for k, v in config.env.items() if isinstance(k, str)})

        # Start the process
        try:
            process = await asyncio.create_subprocess_exec(
                *config.command,
                *config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )

            server_state.process = process
            server_state.connection_info = {
                "pid": process.pid,
                "command": config.command,
                "args": config.args
            }

            logger.debug(f"Started stdio server {config.name} with PID {process.pid}")

        except Exception as e:
            raise RuntimeError(f"Failed to start stdio process: {e}")

    async def _start_http_server(self, server_state: ServerState) -> None:
        """Start connection to an HTTP-based MCP server."""
        config = server_state.config

        if not config.url:
            raise ValueError("HTTP transport requires a URL")

        # TODO: Implement HTTP connection using FastMCP
        # For now, just store connection info
        server_state.connection_info = {
            "url": config.url,
            "transport": "http"
        }

        logger.debug(f"Connected to HTTP server {config.name} at {config.url}")

    async def _start_sse_server(self, server_state: ServerState) -> None:
        """Start connection to an SSE-based MCP server."""
        config = server_state.config

        if not config.url:
            raise ValueError("SSE transport requires a URL")

        # TODO: Implement SSE connection using FastMCP
        # For now, just store connection info
        server_state.connection_info = {
            "url": config.url,
            "transport": "sse"
        }

        logger.debug(f"Connected to SSE server {config.name} at {config.url}")

    async def stop_all_servers(self) -> None:
        """Stop all servers."""
        logger.info("Stopping all servers...")

        # Signal shutdown
        self._shutdown_event.set()

        # Stop monitoring task
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Stop all servers
        stop_tasks = []
        for server_name in self.servers:
            task = asyncio.create_task(self.stop_server(server_name))
            stop_tasks.append(task)

        await asyncio.gather(*stop_tasks, return_exceptions=True)
        logger.info("All servers stopped")

    async def stop_server(self, server_name: str) -> None:
        """Stop a specific server."""
        if server_name not in self.servers:
            return

        server_state = self.servers[server_name]
        logger.info(f"Stopping server: {server_name}")

        if server_state.process:
            try:
                server_state.process.terminate()
                await asyncio.wait_for(server_state.process.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                logger.warning(f"Server {server_name} did not stop gracefully, killing...")
                server_state.process.kill()
                await server_state.process.wait()
            except Exception as e:
                logger.error(f"Error stopping server {server_name}: {e}")

        server_state.status = ServerStatus.STOPPED
        server_state.process = None
        server_state.connection_info.clear()

    async def restart_server(self, server_name: str) -> bool:
        """Restart a specific server."""
        logger.info(f"Restarting server: {server_name}")

        await self.stop_server(server_name)
        await asyncio.sleep(1)  # Brief delay before restart

        server_state = self.servers[server_name]
        server_state.restart_count += 1

        return await self.start_server(server_name)

    async def _monitor_servers(self) -> None:
        """Monitor server health and restart failed servers."""
        logger.info("Starting server monitoring...")

        while not self._shutdown_event.is_set():
            try:
                for server_name, server_state in self.servers.items():
                    if server_state.status == ServerStatus.RUNNING:
                        # Check if stdio process is still running
                        if server_state.process:
                            if server_state.process.returncode is not None:
                                logger.warning(f"Server {server_name} process died, restarting...")
                                server_state.status = ServerStatus.ERROR
                                asyncio.create_task(self.restart_server(server_name))

                # Wait before next check
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in server monitoring: {e}")
                await asyncio.sleep(5)

        logger.info("Server monitoring stopped")

    def get_active_servers(self) -> List[str]:
        """Get list of active server names."""
        return [
            name for name, state in self.servers.items()
            if state.status == ServerStatus.RUNNING
        ]

    async def get_all_server_status(self) -> List[Tuple[str, Dict[str, Any]]]:
        """Get status of all servers."""
        status_list = []
        for name, state in self.servers.items():
            status_info = {
                "status": state.status,
                "last_started": state.last_started,
                "last_error": state.last_error,
                "error_count": state.error_count,
                "restart_count": state.restart_count,
                "transport": state.config.transport,
                "enabled": state.config.enabled
            }
            status_list.append((name, status_info))
        return status_list

    async def get_server_details(self) -> Dict[str, Any]:
        """Get detailed information about all servers."""
        details = {}
        for name, state in self.servers.items():
            details[name] = {
                "config": state.config.dict(),
                "status": state.status,
                "connection_info": state.connection_info,
                "runtime_info": {
                    "last_started": state.last_started,
                    "error_count": state.error_count,
                    "restart_count": state.restart_count,
                    "last_error": state.last_error
                }
            }
        return details

    async def get_server_status(self, server_name: str) -> Optional[Dict[str, Any]]:
        """Get status of a specific server."""
        if server_name not in self.servers:
            return None

        state = self.servers[server_name]
        return {
            "name": server_name,
            "status": state.status,
            "config": state.config.dict(),
            "connection_info": state.connection_info,
            "last_started": state.last_started,
            "error_count": state.error_count,
            "restart_count": state.restart_count,
            "last_error": state.last_error
        }