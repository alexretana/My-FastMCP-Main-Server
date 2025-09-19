"""
Tests for session lifecycle management.

Tests the FastMCP event system integration and session lifecycle hooks.
"""

import pytest
import asyncio
from unittest.mock import Mock, AsyncMock, patch
from datetime import datetime, timedelta

from mcp_proxy_server.session_lifecycle import (
    SessionLifecycleManager,
    SessionEvent,
    SessionEventData,
    MetricsCollectionHook,
    LoggingHook,
    SecurityAuditHook,
    SessionLifecycleHook,
    FastMCPSessionContext
)


class MockLifecycleHook(SessionLifecycleHook):
    """Mock lifecycle hook for testing."""

    def __init__(self):
        self.events_received = []

    async def on_session_created(self, event_data: SessionEventData) -> None:
        self.events_received.append(('created', event_data.session_id))

    async def on_session_started(self, event_data: SessionEventData) -> None:
        self.events_received.append(('started', event_data.session_id))

    async def on_request_received(self, event_data: SessionEventData) -> None:
        self.events_received.append(('request', event_data.session_id))

    async def on_error_occurred(self, event_data: SessionEventData) -> None:
        self.events_received.append(('error', event_data.session_id))

    async def on_session_destroyed(self, event_data: SessionEventData) -> None:
        self.events_received.append(('destroyed', event_data.session_id))


class TestSessionEventData:
    """Test SessionEventData dataclass."""

    def test_event_data_creation(self):
        """Test creating session event data."""
        session_id = "test-session"
        event = SessionEvent.CREATED
        timestamp = datetime.now()

        event_data = SessionEventData(
            session_id=session_id,
            event=event,
            timestamp=timestamp,
            client_info={'test': 'data'}
        )

        assert event_data.session_id == session_id
        assert event_data.event == event
        assert event_data.timestamp == timestamp
        assert event_data.client_info == {'test': 'data'}


class TestSessionLifecycleManager:
    """Test the main session lifecycle manager."""

    @pytest.fixture
    def lifecycle_manager(self):
        return SessionLifecycleManager()

    @pytest.fixture
    def mock_hook(self):
        return MockLifecycleHook()

    @pytest.mark.asyncio
    async def test_hook_registration(self, lifecycle_manager, mock_hook):
        """Test adding and removing hooks."""
        initial_count = len(lifecycle_manager.hooks)

        lifecycle_manager.add_hook(mock_hook)
        assert len(lifecycle_manager.hooks) == initial_count + 1

        lifecycle_manager.remove_hook(mock_hook)
        assert len(lifecycle_manager.hooks) == initial_count

    @pytest.mark.asyncio
    async def test_event_emission_to_hooks(self, lifecycle_manager, mock_hook):
        """Test that events are emitted to all hooks."""
        lifecycle_manager.add_hook(mock_hook)
        session_id = "test-session"

        # Emit session created event
        await lifecycle_manager.create_session_event(
            session_id,
            SessionEvent.CREATED
        )

        # Check that hook received the event
        assert ('created', session_id) in mock_hook.events_received

    @pytest.mark.asyncio
    async def test_session_tracking(self, lifecycle_manager):
        """Test that active sessions are tracked properly."""
        session_id = "tracking-test"

        # Session should not be tracked initially
        assert session_id not in lifecycle_manager.active_sessions

        # Create session
        await lifecycle_manager.create_session_event(session_id, SessionEvent.CREATED)
        assert session_id in lifecycle_manager.active_sessions

        # Destroy session
        await lifecycle_manager.create_session_event(session_id, SessionEvent.DESTROYED)
        assert session_id not in lifecycle_manager.active_sessions

    @pytest.mark.asyncio
    async def test_event_data_propagation(self, lifecycle_manager, mock_hook):
        """Test that event data is properly propagated to hooks."""
        lifecycle_manager.add_hook(mock_hook)
        session_id = "data-test"

        client_info = {'user_agent': 'test-client', 'ip': '127.0.0.1'}
        request_data = {'type': 'test_request', 'data': 'test'}

        # Create event with data
        event_data = await lifecycle_manager.create_session_event(
            session_id,
            SessionEvent.REQUEST_RECEIVED,
            client_info=client_info,
            request_data=request_data
        )

        # Verify event data properties
        assert event_data.session_id == session_id
        assert event_data.client_info == client_info
        assert event_data.request_data == request_data
        assert isinstance(event_data.timestamp, datetime)

    @pytest.mark.asyncio
    async def test_error_handling_in_hooks(self, lifecycle_manager):
        """Test that errors in hooks don't crash the system."""
        class ErrorHook(SessionLifecycleHook):
            async def on_session_created(self, event_data: SessionEventData) -> None:
                raise Exception("Test error in hook")

        error_hook = ErrorHook()
        lifecycle_manager.add_hook(error_hook)

        # This should not raise an exception
        await lifecycle_manager.create_session_event("test", SessionEvent.CREATED)

        # Session should still be tracked despite hook error
        assert "test" in lifecycle_manager.active_sessions

    def test_lifecycle_stats(self, lifecycle_manager):
        """Test lifecycle statistics collection."""
        stats = lifecycle_manager.get_lifecycle_stats()

        assert 'active_sessions' in stats
        assert 'active_session_count' in stats
        assert 'registered_hooks' in stats
        assert 'metrics' in stats
        assert 'security_audit' in stats

        # Check that built-in hooks are registered
        hook_names = stats['registered_hooks']
        assert 'MetricsCollectionHook' in hook_names
        assert 'LoggingHook' in hook_names
        assert 'SecurityAuditHook' in hook_names


class TestMetricsCollectionHook:
    """Test the metrics collection hook in detail."""

    @pytest.fixture
    def metrics_hook(self):
        return MetricsCollectionHook()

    @pytest.mark.asyncio
    async def test_session_duration_calculation(self, metrics_hook):
        """Test session duration calculation."""
        session_id = "duration-test"
        start_time = datetime.now()

        # Create session
        create_event = SessionEventData(
            session_id=session_id,
            event=SessionEvent.CREATED,
            timestamp=start_time
        )
        await metrics_hook.on_session_created(create_event)

        # Destroy session after some time
        end_time = start_time + timedelta(seconds=5)
        destroy_event = SessionEventData(
            session_id=session_id,
            event=SessionEvent.DESTROYED,
            timestamp=end_time
        )
        await metrics_hook.on_session_destroyed(destroy_event)

        # Check global metrics were updated
        assert metrics_hook.global_metrics['total_sessions'] == 1

    @pytest.mark.asyncio
    async def test_concurrent_session_metrics(self, metrics_hook):
        """Test metrics with multiple concurrent sessions."""
        # Create multiple sessions
        sessions = ["session1", "session2", "session3"]
        create_time = datetime.now()

        for session_id in sessions:
            event = SessionEventData(
                session_id=session_id,
                event=SessionEvent.CREATED,
                timestamp=create_time
            )
            await metrics_hook.on_session_created(event)

        # Check active session count
        assert metrics_hook.global_metrics['active_sessions'] == 3
        assert len(metrics_hook.session_metrics) == 3

        # Process requests for each session
        for session_id in sessions:
            for i in range(5):  # 5 requests per session
                event = SessionEventData(
                    session_id=session_id,
                    event=SessionEvent.REQUEST_RECEIVED,
                    timestamp=create_time
                )
                await metrics_hook.on_request_received(event)

        # Check request counting
        assert metrics_hook.global_metrics['total_requests'] == 15
        for session_id in sessions:
            assert metrics_hook.session_metrics[session_id]['request_count'] == 5

    def test_metrics_data_structure(self, metrics_hook):
        """Test the structure of metrics data."""
        metrics = metrics_hook.get_metrics()

        # Global metrics structure
        assert 'global' in metrics
        global_metrics = metrics['global']
        required_global_keys = [
            'total_sessions', 'active_sessions', 'total_requests',
            'error_count', 'average_session_duration', 'average_requests_per_session'
        ]
        for key in required_global_keys:
            assert key in global_metrics

        # Active sessions structure
        assert 'active_sessions' in metrics
        assert isinstance(metrics['active_sessions'], dict)


class TestSecurityAuditHook:
    """Test the security audit hook in detail."""

    @pytest.fixture
    def security_hook(self):
        return SecurityAuditHook()

    @pytest.mark.asyncio
    async def test_security_event_structure(self, security_hook):
        """Test the structure of security audit events."""
        session_id = "security-test"
        client_info = {'ip': '192.168.1.100', 'user_agent': 'test-client'}

        event_data = SessionEventData(
            session_id=session_id,
            event=SessionEvent.CREATED,
            timestamp=datetime.now(),
            client_info=client_info
        )

        await security_hook.on_session_created(event_data)

        assert len(security_hook.security_events) == 1
        security_event = security_hook.security_events[0]

        # Check required fields
        assert security_event['type'] == 'session_created'
        assert security_event['session_id'] == session_id
        assert 'timestamp' in security_event
        assert security_event['client_info'] == client_info
        assert security_event['security_level'] == 'info'

    @pytest.mark.asyncio
    async def test_suspicious_pattern_detection(self, security_hook):
        """Test detection of suspicious request patterns."""
        session_id = "pattern-test"
        request_data = {'type': 'rapid_request'}

        # Generate requests above threshold
        for i in range(105):
            event_data = SessionEventData(
                session_id=session_id,
                event=SessionEvent.REQUEST_RECEIVED,
                timestamp=datetime.now(),
                request_data=request_data
            )
            await security_hook.on_request_received(event_data)

        # Check that pattern was detected
        pattern_key = f"{session_id}:rapid_request"
        assert pattern_key in security_hook.suspicious_patterns
        assert security_hook.suspicious_patterns[pattern_key] == 105

        # Check that warning event was created
        warning_events = [
            event for event in security_hook.security_events
            if event.get('security_level') == 'warning'
        ]
        assert len(warning_events) > 0

    @pytest.mark.asyncio
    async def test_security_error_tracking(self, security_hook):
        """Test tracking of security-related errors."""
        session_id = "error-test"
        error_info = {
            'type': 'authentication_error',
            'message': 'Invalid credentials',
            'code': 401
        }

        event_data = SessionEventData(
            session_id=session_id,
            event=SessionEvent.ERROR_OCCURRED,
            timestamp=datetime.now(),
            error_info=error_info
        )

        await security_hook.on_error_occurred(event_data)

        # Check that security error was recorded
        security_errors = [
            event for event in security_hook.security_events
            if event.get('type') == 'security_error'
        ]
        assert len(security_errors) == 1
        assert security_errors[0]['error_info'] == error_info

    def test_security_audit_summary(self, security_hook):
        """Test security audit summary generation."""
        audit_data = security_hook.get_security_audit()

        assert 'events' in audit_data
        assert 'suspicious_patterns' in audit_data
        assert 'summary' in audit_data

        summary = audit_data['summary']
        assert 'total_events' in summary
        assert 'security_warnings' in summary
        assert 'security_errors' in summary


class TestFastMCPSessionContext:
    """Test FastMCP session context integration."""

    @pytest.fixture
    def session_context(self):
        lifecycle_manager = SessionLifecycleManager()
        return FastMCPSessionContext(lifecycle_manager)

    @pytest.mark.asyncio
    async def test_session_context_creation(self, session_context):
        """Test creating a session context."""
        session_id = "context-test"
        client_info = {'client_type': 'test', 'version': '1.0'}

        # Mock the Context creation to avoid FastMCP dependencies
        with patch('mcp_proxy_server.session_lifecycle.Context') as mock_context:
            mock_context.return_value = Mock()
            context = await session_context.create_session_context(session_id, client_info)

        # Check that lifecycle events were emitted
        assert session_id in session_context.lifecycle_manager.active_sessions

        # Check metrics were updated
        metrics = session_context.lifecycle_manager.metrics_hook.get_metrics()
        assert metrics['global']['total_sessions'] == 1

    @pytest.mark.asyncio
    async def test_context_request_handling(self, session_context):
        """Test handling requests through the context."""
        session_id = "request-context-test"
        client_info = {'test': 'data'}

        # Mock the Context creation to avoid FastMCP dependencies
        with patch('mcp_proxy_server.session_lifecycle.Context') as mock_context:
            mock_context.return_value = Mock()
            # Create context
            context = await session_context.create_session_context(session_id, client_info)

            # Handle request
            request_data = {'type': 'test_request', 'data': 'test'}
            result = await session_context.handle_context_request(context, session_id, request_data)

        assert result is not None
        assert 'status' in result

        # Check that request was tracked
        metrics = session_context.lifecycle_manager.metrics_hook.get_metrics()
        assert metrics['global']['total_requests'] == 1

    @pytest.mark.asyncio
    async def test_context_error_handling(self, session_context):
        """Test error handling in context requests."""
        session_id = "error-context-test"
        client_info = {'test': 'data'}

        # Mock the Context creation to avoid FastMCP dependencies
        with patch('mcp_proxy_server.session_lifecycle.Context') as mock_context:
            mock_context.return_value = Mock()
            # Create context
            context = await session_context.create_session_context(session_id, client_info)

            # Mock an error in request handling
            with pytest.raises(ValueError):
                # Simulate error by passing invalid data
                await session_context.handle_context_request(context, session_id, None)

        # Check that error was tracked
        metrics = session_context.lifecycle_manager.metrics_hook.get_metrics()
        assert metrics['global']['error_count'] == 1

    @pytest.mark.asyncio
    async def test_context_cleanup(self, session_context):
        """Test context cleanup."""
        session_id = "cleanup-context-test"
        client_info = {'test': 'data'}

        # Mock the Context creation to avoid FastMCP dependencies
        with patch('mcp_proxy_server.session_lifecycle.Context') as mock_context:
            mock_context.return_value = Mock()
            # Create context
            await session_context.create_session_context(session_id, client_info)

        # Verify session is active
        assert session_id in session_context.lifecycle_manager.active_sessions

        # Clean up context
        await session_context.cleanup_session_context(session_id)

        # Verify session is no longer active
        assert session_id not in session_context.lifecycle_manager.active_sessions


# Integration tests
@pytest.mark.asyncio
async def test_complete_session_lifecycle_integration():
    """Integration test for complete session lifecycle with all hooks."""
    lifecycle_manager = SessionLifecycleManager()
    session_id = "integration-full-test"

    # Track initial metrics
    initial_metrics = lifecycle_manager.metrics_hook.get_metrics()
    initial_sessions = initial_metrics['global']['total_sessions']

    # Full lifecycle simulation
    client_info = {'integration': 'test', 'ip': '127.0.0.1'}

    # 1. Create session
    await lifecycle_manager.create_session_event(
        session_id, SessionEvent.CREATED, client_info=client_info
    )

    # 2. Start session
    await lifecycle_manager.create_session_event(
        session_id, SessionEvent.STARTED
    )

    # 3. Process multiple requests
    for i in range(10):
        await lifecycle_manager.create_session_event(
            session_id,
            SessionEvent.REQUEST_RECEIVED,
            request_data={'type': 'integration_test', 'request_id': i}
        )
        await lifecycle_manager.create_session_event(
            session_id, SessionEvent.REQUEST_COMPLETED
        )

    # 4. Simulate an error
    await lifecycle_manager.create_session_event(
        session_id,
        SessionEvent.ERROR_OCCURRED,
        error_info={'type': 'test_error', 'message': 'Integration test error'}
    )

    # 5. Clean up session
    await lifecycle_manager.create_session_event(
        session_id, SessionEvent.CLEANUP_STARTED
    )
    await lifecycle_manager.create_session_event(
        session_id, SessionEvent.DESTROYED
    )

    # Verify final state
    final_metrics = lifecycle_manager.metrics_hook.get_metrics()
    assert final_metrics['global']['total_sessions'] == initial_sessions + 1
    assert final_metrics['global']['total_requests'] == 10
    assert final_metrics['global']['error_count'] == 1
    assert session_id not in lifecycle_manager.active_sessions

    # Check security audit
    security_audit = lifecycle_manager.security_hook.get_security_audit()
    assert security_audit['summary']['total_events'] > 0


if __name__ == "__main__":
    pytest.main([__file__])