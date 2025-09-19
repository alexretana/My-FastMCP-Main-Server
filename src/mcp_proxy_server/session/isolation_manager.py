"""
Session isolation manager for FastMCP proxy server.

Manages client session isolation using FastMCP session handling.
Ensures each client gets isolated backend sessions and proper cleanup.
"""

import asyncio
import logging
from typing import Dict, Any, Optional

from fastmcp.server.proxy import ProxyClient

from ..config import TransportType
from ..server_registry import ServerRegistry, ServerStatus
from ..session_lifecycle import SessionLifecycleManager, SessionEvent, FastMCPSessionContext

logger = logging.getLogger(__name__)


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
                # FastMCP ProxyClient requires either a string command or MCPConfig dict
                command = server_config['command']
                if isinstance(command, list):
                    # Convert command list to MCPConfig format
                    mcp_config = {
                        'mcpServers': {
                            server_name: {
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
                            server_name: {
                                'command': command,
                                'transport': 'stdio'
                            }
                        }
                    }
                    return ProxyClient(mcp_config)
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