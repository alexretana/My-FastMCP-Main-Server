"""
Tests for FastMCP proxy implementation.

Tests the tool/resource routing and session isolation functionality.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime

from mcp_proxy_server.fastmcp_proxy import (
    FastMCPProxyServer,
    NamespaceRoutingMiddleware,
    RequestTransformationMiddleware,
    SessionIsolationManager
)
from mcp_proxy_server.session_lifecycle import (
    SessionLifecycleManager,
    SessionEvent,
    SessionEventData,
    MetricsCollectionHook,
    LoggingHook,
    SecurityAuditHook
)
from mcp_proxy_server.config import ProxyConfig, ServerConfig, TransportType


@pytest.fixture
def mock_server_registry():
    """Create a mock server registry."""
    registry = Mock()
    registry.get_active_servers.return_value = ["server1", "server2"]
    # Make get_server_status async
    async def mock_get_server_status(server_name):
        return {
            'status': 'running',
            'config': {
                'transport': TransportType.STDIO,
                'command': ['python', 'test_server.py']
            }
        }
    registry.get_server_status = mock_get_server_status
    return registry


@pytest.fixture
def sample_proxy_config():
    """Create a sample proxy configuration."""
    return ProxyConfig(
        name="test-proxy",
        host="localhost",
        port=8000,
        transport="stdio",
        servers=[
            ServerConfig(
                name="server1",
                transport=TransportType.STDIO,
                command=["python", "server1.py"],
                enabled=True
            ),
            ServerConfig(
                name="server2",
                transport=TransportType.HTTP,
                url="http://localhost:8001/mcp",
                enabled=True
            )
        ]
    )


class TestNamespaceRoutingMiddleware:
    """Test namespace-based routing middleware."""

    @pytest.fixture
    def routing_middleware(self, mock_server_registry):
        return NamespaceRoutingMiddleware(mock_server_registry)

    def test_namespace_conflict_resolution(self, routing_middleware):
        """Test that naming conflicts are resolved with prefixes."""
        # Mock tools with conflicting names
        tool1 = Mock()
        tool1.name = "search"
        tool2 = Mock()
        tool2.name = "search"

        tools = [tool1, tool2]

        # Apply conflict resolution
        routing_middleware._resolve_naming_conflicts(tools, "tool")

        # Check that at least one tool got prefixed
        names = [tool.name for tool in tools]
        assert any(":" in name for name in names), "Expected namespace prefix to be applied"

    def test_no_conflict_no_prefix(self, routing_middleware):
        """Test that unique names don't get prefixed."""
        tool1 = Mock()
        tool1.name = "search"
        tool2 = Mock()
        tool2.name = "weather"

        tools = [tool1, tool2]
        original_names = [tool.name for tool in tools]

        routing_middleware._resolve_naming_conflicts(tools, "tool")

        # Names should remain unchanged
        new_names = [tool.name for tool in tools]
        assert new_names == original_names


class TestSessionIsolationManager:
    """Test session isolation functionality."""

    @pytest.fixture
    def session_manager(self, mock_server_registry):
        return SessionIsolationManager(mock_server_registry)

    @pytest.mark.asyncio
    async def test_session_creation(self, session_manager):
        """Test that sessions are created properly."""
        client_id = "test-client-1"

        # Mock the backend client creation to avoid ProxyClient instantiation
        with patch.object(session_manager, '_create_backend_client', new=AsyncMock(return_value=Mock())):
            session_data = await session_manager.get_or_create_client_session(client_id)

        assert client_id in session_manager.client_sessions
        assert 'created_at' in session_data
        assert 'last_activity' in session_data
        assert 'request_count' in session_data

    @pytest.mark.asyncio
    async def test_session_isolation(self, session_manager):
        """Test that different clients get isolated sessions."""
        client1 = "client-1"
        client2 = "client-2"

        # Mock the backend client creation to avoid ProxyClient instantiation
        with patch.object(session_manager, '_create_backend_client', new=AsyncMock(return_value=Mock())):
            await session_manager.get_or_create_client_session(client1)
            await session_manager.get_or_create_client_session(client2)

        # Both clients should have separate sessions
        assert client1 in session_manager.client_sessions
        assert client2 in session_manager.client_sessions
        assert len(session_manager.client_sessions) == 2

        # Each should have separate backend clients
        assert client1 in session_manager.backend_clients
        assert client2 in session_manager.backend_clients

    @pytest.mark.asyncio
    async def test_session_activity_update(self, session_manager):
        """Test that session activity is tracked."""
        client_id = "test-client"

        # Mock the backend client creation to avoid ProxyClient instantiation
        with patch.object(session_manager, '_create_backend_client', new=AsyncMock(return_value=Mock())):
            await session_manager.get_or_create_client_session(client_id)

        original_count = session_manager.client_sessions[client_id]['request_count']
        original_activity = session_manager.client_sessions[client_id]['last_activity']

        # Wait a bit to ensure time difference
        await asyncio.sleep(0.01)

        await session_manager.update_session_activity(client_id)

        assert session_manager.client_sessions[client_id]['request_count'] == original_count + 1
        assert session_manager.client_sessions[client_id]['last_activity'] > original_activity

    @pytest.mark.asyncio
    async def test_session_cleanup(self, session_manager):
        """Test that sessions are cleaned up properly."""
        client_id = "test-client"

        # Mock the backend client creation to avoid ProxyClient instantiation
        with patch.object(session_manager, '_create_backend_client', new=AsyncMock(return_value=Mock())):
            await session_manager.get_or_create_client_session(client_id)

        # Verify session exists
        assert client_id in session_manager.client_sessions

        # Clean up session
        await session_manager.cleanup_session(client_id)

        # Verify session is removed
        assert client_id not in session_manager.client_sessions
        assert client_id not in session_manager.backend_clients
        assert client_id not in session_manager.session_locks

    @pytest.mark.asyncio
    async def test_idle_session_cleanup(self, session_manager):
        """Test that idle sessions are cleaned up automatically."""
        client_id = "idle-client"

        # Mock the backend client creation to avoid ProxyClient instantiation
        with patch.object(session_manager, '_create_backend_client', new=AsyncMock(return_value=Mock())):
            await session_manager.get_or_create_client_session(client_id)

        # Manually set old activity time
        session_manager.client_sessions[client_id]['last_activity'] = 0

        # Clean up idle sessions with very short timeout
        await session_manager.cleanup_idle_sessions(max_idle_time=1)

        # Session should be cleaned up
        assert client_id not in session_manager.client_sessions


class TestSessionLifecycleManager:
    """Test session lifecycle management."""

    @pytest.fixture
    def lifecycle_manager(self):
        return SessionLifecycleManager()

    @pytest.mark.asyncio
    async def test_session_event_emission(self, lifecycle_manager):
        """Test that session events are emitted properly."""
        session_id = "test-session"

        # Create session event
        event_data = await lifecycle_manager.create_session_event(
            session_id,
            SessionEvent.CREATED,
            client_info={'user_agent': 'test-client'}
        )

        assert event_data.session_id == session_id
        assert event_data.event == SessionEvent.CREATED
        assert event_data.client_info['user_agent'] == 'test-client'
        assert session_id in lifecycle_manager.active_sessions

    @pytest.mark.asyncio
    async def test_session_lifecycle_tracking(self, lifecycle_manager):
        """Test complete session lifecycle tracking."""
        session_id = "lifecycle-test"

        # Session created
        await lifecycle_manager.create_session_event(session_id, SessionEvent.CREATED)
        assert session_id in lifecycle_manager.active_sessions

        # Session destroyed
        await lifecycle_manager.create_session_event(session_id, SessionEvent.DESTROYED)
        assert session_id not in lifecycle_manager.active_sessions

    def test_metrics_collection(self, lifecycle_manager):
        """Test that metrics are collected properly."""
        metrics_hook = lifecycle_manager.metrics_hook
        assert metrics_hook is not None

        initial_metrics = metrics_hook.get_metrics()
        assert 'global' in initial_metrics
        assert 'active_sessions' in initial_metrics

    def test_security_audit(self, lifecycle_manager):
        """Test security audit functionality."""
        security_hook = lifecycle_manager.security_hook
        assert security_hook is not None

        audit_data = security_hook.get_security_audit()
        assert 'events' in audit_data
        assert 'suspicious_patterns' in audit_data
        assert 'summary' in audit_data


class TestMetricsCollectionHook:
    """Test metrics collection hook."""

    @pytest.fixture
    def metrics_hook(self):
        return MetricsCollectionHook()

    @pytest.mark.asyncio
    async def test_session_metrics_initialization(self, metrics_hook):
        """Test that session metrics are initialized properly."""
        session_id = "metrics-test"
        event_data = SessionEventData(
            session_id=session_id,
            event=SessionEvent.CREATED,
            timestamp=datetime.now(),
            client_info={'test': 'data'}
        )

        await metrics_hook.on_session_created(event_data)

        assert session_id in metrics_hook.session_metrics
        assert metrics_hook.global_metrics['total_sessions'] == 1
        assert metrics_hook.global_metrics['active_sessions'] == 1

    @pytest.mark.asyncio
    async def test_request_counting(self, metrics_hook):
        """Test that requests are counted properly."""
        session_id = "request-test"

        # Initialize session
        create_event = SessionEventData(
            session_id=session_id,
            event=SessionEvent.CREATED,
            timestamp=datetime.now()
        )
        await metrics_hook.on_session_created(create_event)

        # Process request
        request_event = SessionEventData(
            session_id=session_id,
            event=SessionEvent.REQUEST_RECEIVED,
            timestamp=datetime.now()
        )
        await metrics_hook.on_request_received(request_event)

        assert metrics_hook.session_metrics[session_id]['request_count'] == 1
        assert metrics_hook.global_metrics['total_requests'] == 1

    @pytest.mark.asyncio
    async def test_error_tracking(self, metrics_hook):
        """Test that errors are tracked properly."""
        session_id = "error-test"

        # Initialize session
        create_event = SessionEventData(
            session_id=session_id,
            event=SessionEvent.CREATED,
            timestamp=datetime.now()
        )
        await metrics_hook.on_session_created(create_event)

        # Process error
        error_event = SessionEventData(
            session_id=session_id,
            event=SessionEvent.ERROR_OCCURRED,
            timestamp=datetime.now(),
            error_info={'type': 'test_error', 'message': 'Test error'}
        )
        await metrics_hook.on_error_occurred(error_event)

        assert metrics_hook.session_metrics[session_id]['error_count'] == 1
        assert metrics_hook.global_metrics['error_count'] == 1


class TestSecurityAuditHook:
    """Test security audit hook."""

    @pytest.fixture
    def security_hook(self):
        return SecurityAuditHook()

    @pytest.mark.asyncio
    async def test_security_event_logging(self, security_hook):
        """Test that security events are logged."""
        session_id = "security-test"
        event_data = SessionEventData(
            session_id=session_id,
            event=SessionEvent.CREATED,
            timestamp=datetime.now(),
            client_info={'ip': '127.0.0.1'}
        )

        await security_hook.on_session_created(event_data)

        assert len(security_hook.security_events) == 1
        assert security_hook.security_events[0]['type'] == 'session_created'
        assert security_hook.security_events[0]['session_id'] == session_id

    @pytest.mark.asyncio
    async def test_suspicious_activity_detection(self, security_hook):
        """Test that suspicious activity is detected."""
        session_id = "suspicious-test"

        # Simulate many requests
        for i in range(105):  # Above threshold of 100
            event_data = SessionEventData(
                session_id=session_id,
                event=SessionEvent.REQUEST_RECEIVED,
                timestamp=datetime.now(),
                request_data={'type': 'test_request'}
            )
            await security_hook.on_request_received(event_data)

        # Check that suspicious activity was flagged
        suspicious_events = [
            event for event in security_hook.security_events
            if event.get('type') == 'suspicious_activity'
        ]
        assert len(suspicious_events) > 0


class TestFastMCPProxyServer:
    """Test the main FastMCP proxy server."""

    @pytest.fixture
    def proxy_server(self, sample_proxy_config, mock_server_registry):
        with patch('mcp_proxy_server.fastmcp_proxy.ServerRegistry', return_value=mock_server_registry):
            return FastMCPProxyServer(sample_proxy_config, {})

    @pytest.mark.asyncio
    async def test_proxy_initialization(self, proxy_server):
        """Test that the proxy server initializes properly."""
        with patch.object(proxy_server.server_registry, 'initialize', new=AsyncMock()), \
             patch.object(proxy_server, '_create_proxy_clients', new=AsyncMock()), \
             patch.object(proxy_server, '_create_composite_proxy', new=AsyncMock()), \
             patch('mcp_proxy_server.fastmcp_proxy.FastMCP'):

            await proxy_server.initialize()

            assert proxy_server.fastmcp_server is not None
            assert proxy_server.session_manager is not None
            assert proxy_server.routing_middleware is not None
            assert proxy_server.transformation_middleware is not None

    @pytest.mark.asyncio
    async def test_request_handling_with_session_isolation(self, proxy_server):
        """Test that requests are handled with proper session isolation."""
        client_id = "test-client"
        request_data = {
            'name': 'test_tool',
            'arguments': {'param1': 'value1'}
        }

        # Mock the necessary components
        proxy_server.composite_proxy = Mock()
        proxy_server.session_manager = Mock()
        proxy_server.session_manager.get_or_create_client_session = AsyncMock()
        proxy_server.session_manager.update_session_activity = AsyncMock()
        proxy_server._handle_tool_call = AsyncMock(return_value={'result': 'success'})

        result = await proxy_server.handle_request(client_id, "tools/call", request_data)

        # Verify session management was called
        proxy_server.session_manager.get_or_create_client_session.assert_called_once_with(client_id)
        proxy_server.session_manager.update_session_activity.assert_called_once_with(client_id, request_data)

        assert result['result'] == 'success'

    def test_proxy_stats_collection(self, proxy_server):
        """Test that proxy statistics are collected properly."""
        # Mock session manager stats
        proxy_server.session_manager = Mock()
        proxy_server.session_manager.get_session_stats.return_value = {
            'active_sessions': 2,
            'lifecycle': {'active_session_count': 2}
        }

        proxy_server.server_registry = Mock()
        proxy_server.server_registry.get_active_servers.return_value = ['server1', 'server2']

        stats = proxy_server.get_proxy_stats()

        assert 'proxy' in stats
        assert 'session_management' in stats
        assert 'routing' in stats
        assert stats['proxy']['backend_servers'] == 0  # No proxy clients in test
        assert stats['proxy']['active_servers'] == 2


# Integration test
@pytest.mark.asyncio
async def test_full_session_lifecycle_integration():
    """Integration test for full session lifecycle."""
    # This would be a more comprehensive test in a real scenario
    # For now, just test basic component integration

    lifecycle_manager = SessionLifecycleManager()
    session_id = "integration-test"

    # Create session
    await lifecycle_manager.create_session_event(
        session_id,
        SessionEvent.CREATED,
        client_info={'integration': 'test'}
    )

    # Simulate some activity
    await lifecycle_manager.create_session_event(
        session_id,
        SessionEvent.REQUEST_RECEIVED,
        request_data={'type': 'test'}
    )

    # Destroy session
    await lifecycle_manager.create_session_event(
        session_id,
        SessionEvent.DESTROYED
    )

    # Check metrics
    metrics = lifecycle_manager.metrics_hook.get_metrics()
    assert metrics['global']['total_sessions'] == 1
    assert session_id not in lifecycle_manager.active_sessions


if __name__ == "__main__":
    pytest.main([__file__])