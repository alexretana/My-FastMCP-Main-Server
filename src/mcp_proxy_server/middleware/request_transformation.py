"""
Request transformation middleware for FastMCP proxy server.

Handles request modification, validation, and response formatting.
"""

import logging
from typing import Dict, Any

from fastmcp.server.middleware import Middleware, MiddlewareContext

from ..config import ProxyConfig

logger = logging.getLogger(__name__)


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