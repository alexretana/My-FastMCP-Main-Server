"""
Session lifecycle management with FastMCP event system integration.

This module provides session lifecycle hooks and event handling for the
FastMCP proxy server.
"""

import asyncio
import logging
from typing import Dict, Any, List, Optional, Callable, Awaitable, Set
from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime
import json

from fastmcp import Context
from fastmcp.exceptions import FastMCPError

logger = logging.getLogger(__name__)


class SessionEvent(str, Enum):
    """Session lifecycle events."""
    CREATED = "session_created"
    STARTED = "session_started"
    REQUEST_RECEIVED = "request_received"
    REQUEST_COMPLETED = "request_completed"
    ERROR_OCCURRED = "error_occurred"
    IDLE_TIMEOUT = "idle_timeout"
    CLEANUP_STARTED = "cleanup_started"
    DESTROYED = "session_destroyed"


@dataclass
class SessionEventData:
    """Data associated with session events."""
    session_id: str
    event: SessionEvent
    timestamp: datetime
    client_info: Dict[str, Any] = field(default_factory=dict)
    request_data: Optional[Dict[str, Any]] = None
    error_info: Optional[Dict[str, Any]] = None
    metrics: Dict[str, Any] = field(default_factory=dict)


class SessionLifecycleHook:
    """Base class for session lifecycle hooks."""

    async def on_session_created(self, event_data: SessionEventData) -> None:
        """Called when a new session is created."""
        pass

    async def on_session_started(self, event_data: SessionEventData) -> None:
        """Called when a session becomes active."""
        pass

    async def on_request_received(self, event_data: SessionEventData) -> None:
        """Called when a request is received for a session."""
        pass

    async def on_request_completed(self, event_data: SessionEventData) -> None:
        """Called when a request is completed for a session."""
        pass

    async def on_error_occurred(self, event_data: SessionEventData) -> None:
        """Called when an error occurs in a session."""
        pass

    async def on_idle_timeout(self, event_data: SessionEventData) -> None:
        """Called when a session times out due to inactivity."""
        pass

    async def on_cleanup_started(self, event_data: SessionEventData) -> None:
        """Called when session cleanup begins."""
        pass

    async def on_session_destroyed(self, event_data: SessionEventData) -> None:
        """Called when a session is destroyed."""
        pass


class MetricsCollectionHook(SessionLifecycleHook):
    """Hook for collecting session metrics."""

    def __init__(self):
        self.session_metrics: Dict[str, Dict[str, Any]] = {}
        self.global_metrics = {
            'total_sessions': 0,
            'active_sessions': 0,
            'total_requests': 0,
            'error_count': 0,
            'average_session_duration': 0.0,
            'average_requests_per_session': 0.0
        }

    async def on_session_created(self, event_data: SessionEventData) -> None:
        """Initialize metrics for a new session."""
        self.session_metrics[event_data.session_id] = {
            'created_at': event_data.timestamp,
            'started_at': None,
            'request_count': 0,
            'error_count': 0,
            'last_activity': event_data.timestamp,
            'duration': 0.0,
            'client_info': event_data.client_info.copy()
        }
        self.global_metrics['total_sessions'] += 1
        self.global_metrics['active_sessions'] += 1

        logger.debug(f"Metrics initialized for session {event_data.session_id}")

    async def on_session_started(self, event_data: SessionEventData) -> None:
        """Record session start time."""
        if event_data.session_id in self.session_metrics:
            self.session_metrics[event_data.session_id]['started_at'] = event_data.timestamp

    async def on_request_received(self, event_data: SessionEventData) -> None:
        """Update request metrics."""
        if event_data.session_id in self.session_metrics:
            self.session_metrics[event_data.session_id]['request_count'] += 1
            self.session_metrics[event_data.session_id]['last_activity'] = event_data.timestamp

        self.global_metrics['total_requests'] += 1

    async def on_error_occurred(self, event_data: SessionEventData) -> None:
        """Update error metrics."""
        if event_data.session_id in self.session_metrics:
            self.session_metrics[event_data.session_id]['error_count'] += 1

        self.global_metrics['error_count'] += 1

    async def on_session_destroyed(self, event_data: SessionEventData) -> None:
        """Finalize session metrics."""
        if event_data.session_id in self.session_metrics:
            session_data = self.session_metrics[event_data.session_id]
            if session_data['created_at']:
                duration = (event_data.timestamp - session_data['created_at']).total_seconds()
                session_data['duration'] = duration

            # Update global averages
            self._update_global_averages()

            # Clean up session metrics (keep for historical analysis in real implementation)
            del self.session_metrics[event_data.session_id]

        self.global_metrics['active_sessions'] = max(0, self.global_metrics['active_sessions'] - 1)

    def _update_global_averages(self) -> None:
        """Update global average metrics."""
        if self.global_metrics['total_sessions'] > 0:
            total_duration = sum(
                session['duration'] for session in self.session_metrics.values()
                if session['duration'] > 0
            )
            active_sessions = len([s for s in self.session_metrics.values() if s['duration'] > 0])

            if active_sessions > 0:
                self.global_metrics['average_session_duration'] = total_duration / active_sessions

            self.global_metrics['average_requests_per_session'] = (
                self.global_metrics['total_requests'] / self.global_metrics['total_sessions']
            )

    def get_metrics(self) -> Dict[str, Any]:
        """Get current metrics."""
        return {
            'global': self.global_metrics.copy(),
            'active_sessions': {
                session_id: session_data.copy()
                for session_id, session_data in self.session_metrics.items()
            }
        }


class LoggingHook(SessionLifecycleHook):
    """Hook for structured session logging."""

    def __init__(self, log_level: int = logging.INFO):
        self.log_level = log_level

    async def on_session_created(self, event_data: SessionEventData) -> None:
        """Log session creation."""
        logger.log(
            self.log_level,
            f"Session created: {event_data.session_id}",
            extra={
                'session_id': event_data.session_id,
                'event': event_data.event,
                'timestamp': event_data.timestamp.isoformat(),
                'client_info': event_data.client_info
            }
        )

    async def on_request_received(self, event_data: SessionEventData) -> None:
        """Log request received."""
        logger.debug(
            f"Request received for session {event_data.session_id}",
            extra={
                'session_id': event_data.session_id,
                'event': event_data.event,
                'request_type': event_data.request_data.get('type') if event_data.request_data else None,
                'timestamp': event_data.timestamp.isoformat()
            }
        )

    async def on_error_occurred(self, event_data: SessionEventData) -> None:
        """Log session errors."""
        logger.error(
            f"Error in session {event_data.session_id}: {event_data.error_info}",
            extra={
                'session_id': event_data.session_id,
                'event': event_data.event,
                'error_info': event_data.error_info,
                'timestamp': event_data.timestamp.isoformat()
            }
        )

    async def on_session_destroyed(self, event_data: SessionEventData) -> None:
        """Log session destruction."""
        logger.log(
            self.log_level,
            f"Session destroyed: {event_data.session_id}",
            extra={
                'session_id': event_data.session_id,
                'event': event_data.event,
                'timestamp': event_data.timestamp.isoformat(),
                'metrics': event_data.metrics
            }
        )


class SecurityAuditHook(SessionLifecycleHook):
    """Hook for security audit logging."""

    def __init__(self):
        self.security_events: List[Dict[str, Any]] = []
        self.suspicious_patterns: Dict[str, int] = {}

    async def on_session_created(self, event_data: SessionEventData) -> None:
        """Audit session creation."""
        audit_event = {
            'type': 'session_created',
            'session_id': event_data.session_id,
            'timestamp': event_data.timestamp.isoformat(),
            'client_info': event_data.client_info,
            'security_level': 'info'
        }
        self.security_events.append(audit_event)

    async def on_request_received(self, event_data: SessionEventData) -> None:
        """Audit request patterns for security."""
        if event_data.request_data:
            request_type = event_data.request_data.get('type', 'unknown')

            # Track request patterns per session
            pattern_key = f"{event_data.session_id}:{request_type}"
            self.suspicious_patterns[pattern_key] = self.suspicious_patterns.get(pattern_key, 0) + 1

            # Flag suspicious activity (e.g., too many requests)
            if self.suspicious_patterns[pattern_key] > 100:  # Configurable threshold
                audit_event = {
                    'type': 'suspicious_activity',
                    'session_id': event_data.session_id,
                    'timestamp': event_data.timestamp.isoformat(),
                    'pattern': request_type,
                    'count': self.suspicious_patterns[pattern_key],
                    'security_level': 'warning'
                }
                self.security_events.append(audit_event)
                logger.warning(f"Suspicious activity detected for session {event_data.session_id}")

    async def on_error_occurred(self, event_data: SessionEventData) -> None:
        """Audit security-relevant errors."""
        if event_data.error_info:
            error_type = event_data.error_info.get('type', 'unknown')

            # Track authentication or authorization errors
            if any(keyword in error_type.lower() for keyword in ['auth', 'permission', 'access']):
                audit_event = {
                    'type': 'security_error',
                    'session_id': event_data.session_id,
                    'timestamp': event_data.timestamp.isoformat(),
                    'error_info': event_data.error_info,
                    'security_level': 'error'
                }
                self.security_events.append(audit_event)

    def get_security_audit(self) -> Dict[str, Any]:
        """Get security audit data."""
        return {
            'events': self.security_events.copy(),
            'suspicious_patterns': self.suspicious_patterns.copy(),
            'summary': {
                'total_events': len(self.security_events),
                'security_warnings': len([e for e in self.security_events if e.get('security_level') == 'warning']),
                'security_errors': len([e for e in self.security_events if e.get('security_level') == 'error'])
            }
        }


class SessionLifecycleManager:
    """
    Manages session lifecycle events and hooks with FastMCP event system integration.
    """

    def __init__(self):
        self.hooks: List[SessionLifecycleHook] = []
        self.active_sessions: Set[str] = set()

        # Add built-in hooks
        self.metrics_hook = MetricsCollectionHook()
        self.logging_hook = LoggingHook()
        self.security_hook = SecurityAuditHook()

        self.add_hook(self.metrics_hook)
        self.add_hook(self.logging_hook)
        self.add_hook(self.security_hook)

    def add_hook(self, hook: SessionLifecycleHook) -> None:
        """Add a lifecycle hook."""
        self.hooks.append(hook)
        logger.debug(f"Added session lifecycle hook: {hook.__class__.__name__}")

    def remove_hook(self, hook: SessionLifecycleHook) -> None:
        """Remove a lifecycle hook."""
        if hook in self.hooks:
            self.hooks.remove(hook)
            logger.debug(f"Removed session lifecycle hook: {hook.__class__.__name__}")

    async def emit_event(self, event_data: SessionEventData) -> None:
        """Emit a session lifecycle event to all hooks."""
        # Track active sessions
        if event_data.event == SessionEvent.CREATED:
            self.active_sessions.add(event_data.session_id)
        elif event_data.event == SessionEvent.DESTROYED:
            self.active_sessions.discard(event_data.session_id)

        # Call appropriate hook methods
        hook_tasks = []
        for hook in self.hooks:
            try:
                if event_data.event == SessionEvent.CREATED:
                    hook_tasks.append(hook.on_session_created(event_data))
                elif event_data.event == SessionEvent.STARTED:
                    hook_tasks.append(hook.on_session_started(event_data))
                elif event_data.event == SessionEvent.REQUEST_RECEIVED:
                    hook_tasks.append(hook.on_request_received(event_data))
                elif event_data.event == SessionEvent.REQUEST_COMPLETED:
                    hook_tasks.append(hook.on_request_completed(event_data))
                elif event_data.event == SessionEvent.ERROR_OCCURRED:
                    hook_tasks.append(hook.on_error_occurred(event_data))
                elif event_data.event == SessionEvent.IDLE_TIMEOUT:
                    hook_tasks.append(hook.on_idle_timeout(event_data))
                elif event_data.event == SessionEvent.CLEANUP_STARTED:
                    hook_tasks.append(hook.on_cleanup_started(event_data))
                elif event_data.event == SessionEvent.DESTROYED:
                    hook_tasks.append(hook.on_session_destroyed(event_data))

            except Exception as e:
                logger.error(f"Error in lifecycle hook {hook.__class__.__name__}: {e}")

        # Execute all hook tasks concurrently
        if hook_tasks:
            await asyncio.gather(*hook_tasks, return_exceptions=True)

    async def create_session_event(
        self,
        session_id: str,
        event: SessionEvent,
        client_info: Optional[Dict[str, Any]] = None,
        request_data: Optional[Dict[str, Any]] = None,
        error_info: Optional[Dict[str, Any]] = None,
        metrics: Optional[Dict[str, Any]] = None
    ) -> SessionEventData:
        """Create and emit a session event."""
        event_data = SessionEventData(
            session_id=session_id,
            event=event,
            timestamp=datetime.now(),
            client_info=client_info or {},
            request_data=request_data,
            error_info=error_info,
            metrics=metrics or {}
        )

        await self.emit_event(event_data)
        return event_data

    def get_lifecycle_stats(self) -> Dict[str, Any]:
        """Get lifecycle management statistics."""
        return {
            'active_sessions': list(self.active_sessions),
            'active_session_count': len(self.active_sessions),
            'registered_hooks': [hook.__class__.__name__ for hook in self.hooks],
            'metrics': self.metrics_hook.get_metrics(),
            'security_audit': self.security_hook.get_security_audit()
        }

    async def cleanup_session_resources(self, session_id: str) -> None:
        """Clean up resources for a session with proper lifecycle events."""
        try:
            # Emit cleanup started event
            await self.create_session_event(session_id, SessionEvent.CLEANUP_STARTED)

            # Perform actual cleanup (this would be implemented by the caller)
            logger.info(f"Cleaning up resources for session {session_id}")

            # Emit session destroyed event
            await self.create_session_event(session_id, SessionEvent.DESTROYED)

        except Exception as e:
            logger.error(f"Error during session cleanup for {session_id}: {e}")
            await self.create_session_event(
                session_id,
                SessionEvent.ERROR_OCCURRED,
                error_info={'type': 'cleanup_error', 'message': str(e)}
            )


# Integration helper for FastMCP Context
class FastMCPSessionContext:
    """
    Helper class to integrate session lifecycle with FastMCP Context.
    """

    def __init__(self, lifecycle_manager: SessionLifecycleManager):
        self.lifecycle_manager = lifecycle_manager

    async def create_session_context(self, session_id: str, client_info: Dict[str, Any]) -> Context:
        """Create a FastMCP context with session lifecycle integration."""
        # Emit session created event
        await self.lifecycle_manager.create_session_event(
            session_id,
            SessionEvent.CREATED,
            client_info=client_info
        )

        # Create FastMCP context
        # This is a simplified example - in a real implementation,
        # you'd create an actual FastMCP Context with proper configuration
        context = Context()

        # Emit session started event
        await self.lifecycle_manager.create_session_event(
            session_id,
            SessionEvent.STARTED
        )

        return context

    async def handle_context_request(
        self,
        context: Context,
        session_id: str,
        request_data: Dict[str, Any]
    ) -> Any:
        """Handle a request through the context with lifecycle events."""
        try:
            # Validate request data
            if request_data is None:
                raise ValueError("Request data cannot be None")

            # Emit request received event
            await self.lifecycle_manager.create_session_event(
                session_id,
                SessionEvent.REQUEST_RECEIVED,
                request_data=request_data
            )

            # Handle the actual request (placeholder)
            # In a real implementation, this would use the FastMCP context
            result = {"status": "handled", "request": request_data}

            # Emit request completed event
            await self.lifecycle_manager.create_session_event(
                session_id,
                SessionEvent.REQUEST_COMPLETED,
                metrics={'response_size': len(str(result))}
            )

            return result

        except Exception as e:
            # Emit error event
            await self.lifecycle_manager.create_session_event(
                session_id,
                SessionEvent.ERROR_OCCURRED,
                error_info={'type': 'request_error', 'message': str(e)}
            )
            raise

    async def cleanup_session_context(self, session_id: str) -> None:
        """Clean up a session context with proper lifecycle events."""
        await self.lifecycle_manager.cleanup_session_resources(session_id)