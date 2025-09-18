"""
Configuration management for MCP Proxy Server.

This module provides Pydantic models for configuration validation and loading,
with support for environment variable substitution and deployment-specific settings.
"""

import os
import json
from pathlib import Path
from typing import Dict, List, Optional, Union, Any, Literal
from enum import Enum

from pydantic import BaseModel, Field, validator, model_validator
import yaml


class DeploymentMethod(str, Enum):
    """Supported deployment methods."""
    UV_INSTALL = "uv_install"
    UVX_RUN = "uvx_run"
    DOCKER = "docker"
    MANUAL = "manual"


class TransportType(str, Enum):
    """Supported transport types for MCP communication."""
    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class LogLevel(str, Enum):
    """Supported log levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
    CRITICAL = "CRITICAL"


class ServerConfig(BaseModel):
    """Configuration for a backend MCP server."""

    name: str = Field(..., description="Unique name for this server")
    command: Optional[List[str]] = Field(None, description="Command to start stdio server")
    transport: TransportType = Field(TransportType.STDIO, description="Transport type")
    url: Optional[str] = Field(None, description="URL for HTTP/SSE servers")
    env: Dict[str, str] = Field(default_factory=dict, description="Environment variables")
    args: List[str] = Field(default_factory=list, description="Additional arguments")
    enabled: bool = Field(True, description="Whether this server is enabled")
    timeout: int = Field(30, description="Connection timeout in seconds")
    retry_attempts: int = Field(3, description="Number of retry attempts")
    retry_delay: float = Field(1.0, description="Delay between retries in seconds")
    namespace: Optional[str] = Field(None, description="Namespace prefix for tools/resources")

    @validator('command')
    def validate_command_for_stdio(cls, v, values):
        """Validate that stdio transport has a command."""
        transport = values.get('transport')
        if transport == TransportType.STDIO and not v:
            raise ValueError("stdio transport requires a command")
        return v

    @validator('url')
    def validate_url_for_http_sse(cls, v, values):
        """Validate that HTTP/SSE transport has a URL."""
        transport = values.get('transport')
        if transport in (TransportType.HTTP, TransportType.SSE) and not v:
            raise ValueError(f"{transport} transport requires a url")
        return v


class AuthConfig(BaseModel):
    """Authentication configuration."""

    enabled: bool = Field(False, description="Whether authentication is enabled")
    jwt_secret: Optional[str] = Field(None, description="JWT secret key")
    api_keys: List[str] = Field(default_factory=list, description="Valid API keys")
    oauth_providers: Dict[str, Dict[str, str]] = Field(
        default_factory=dict,
        description="OAuth provider configurations"
    )


class MonitoringConfig(BaseModel):
    """Monitoring and observability configuration."""

    enabled: bool = Field(True, description="Whether monitoring is enabled")
    prometheus_enabled: bool = Field(False, description="Enable Prometheus metrics")
    prometheus_port: int = Field(9090, description="Prometheus metrics port")
    health_check_enabled: bool = Field(True, description="Enable health check endpoint")
    health_check_path: str = Field("/health", description="Health check endpoint path")
    log_requests: bool = Field(True, description="Log all requests")
    log_responses: bool = Field(False, description="Log all responses")


class ProxyConfig(BaseModel):
    """Main proxy server configuration."""

    # Server configuration
    host: str = Field("localhost", description="Host to bind to")
    port: int = Field(8080, description="Port to bind to")
    transport: TransportType = Field(TransportType.STDIO, description="Primary transport type")

    # Backend servers
    servers: List[ServerConfig] = Field(..., description="Backend MCP servers")

    # Logging
    log_level: LogLevel = Field(LogLevel.INFO, description="Log level")
    log_file: Optional[str] = Field(None, description="Log file path")
    log_format: str = Field(
        "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        description="Log format"
    )

    # Authentication
    auth: AuthConfig = Field(default_factory=AuthConfig, description="Authentication settings")

    # Monitoring
    monitoring: MonitoringConfig = Field(
        default_factory=MonitoringConfig,
        description="Monitoring settings"
    )

    # Performance
    max_concurrent_requests: int = Field(100, description="Maximum concurrent requests")
    request_timeout: int = Field(60, description="Request timeout in seconds")
    connection_pool_size: int = Field(10, description="Connection pool size per server")

    # Features
    enable_caching: bool = Field(False, description="Enable response caching")
    cache_ttl: int = Field(300, description="Cache TTL in seconds")
    enable_request_batching: bool = Field(False, description="Enable request batching")

    # Deployment
    deployment_method: Optional[DeploymentMethod] = Field(
        None,
        description="Detected deployment method"
    )

    @validator('servers')
    def validate_servers_not_empty(cls, v):
        """Ensure at least one server is configured."""
        if not v:
            raise ValueError("At least one server must be configured")
        return v

    @validator('servers')
    def validate_unique_server_names(cls, v):
        """Ensure server names are unique."""
        names = [server.name for server in v]
        if len(names) != len(set(names)):
            raise ValueError("Server names must be unique")
        return v

    @model_validator(mode='after')
    def validate_auth_requirements(self):
        """Validate authentication requirements."""
        if self.auth.enabled:
            if not self.auth.jwt_secret and not self.auth.api_keys:
                raise ValueError(
                    "Authentication is enabled but no jwt_secret or api_keys provided"
                )
        return self


class ConfigLoader:
    """Configuration loader with environment variable substitution."""

    @staticmethod
    def detect_deployment_method() -> DeploymentMethod:
        """Detect the current deployment method."""
        # Check for uvx environment
        if os.environ.get('UV_TOOL_DIR') or os.environ.get('VIRTUAL_ENV'):
            # Check if running via uvx
            if 'uvx' in os.environ.get('_', ''):
                return DeploymentMethod.UVX_RUN
            return DeploymentMethod.UV_INSTALL

        # Check for Docker environment
        if os.path.exists('/.dockerenv') or os.environ.get('DOCKER_CONTAINER'):
            return DeploymentMethod.DOCKER

        return DeploymentMethod.MANUAL

    @staticmethod
    def expand_env_vars(data: Any) -> Any:
        """Recursively expand environment variables in configuration data."""
        if isinstance(data, dict):
            return {key: ConfigLoader.expand_env_vars(value) for key, value in data.items()}
        elif isinstance(data, list):
            return [ConfigLoader.expand_env_vars(item) for item in data]
        elif isinstance(data, str):
            return os.path.expandvars(data)
        else:
            return data

    @classmethod
    def load_from_file(cls, config_path: Union[str, Path]) -> ProxyConfig:
        """Load configuration from file with environment variable expansion."""
        config_path = Path(config_path)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load configuration data
        with open(config_path, 'r', encoding='utf-8') as f:
            if config_path.suffix.lower() in ('.yml', '.yaml'):
                data = yaml.safe_load(f)
            else:
                data = json.load(f)

        # Expand environment variables
        data = cls.expand_env_vars(data)

        # Add deployment method detection
        data['deployment_method'] = cls.detect_deployment_method()

        return ProxyConfig(**data)

    @classmethod
    def load_from_dict(cls, data: Dict[str, Any]) -> ProxyConfig:
        """Load configuration from dictionary with environment variable expansion."""
        # Expand environment variables
        data = cls.expand_env_vars(data)

        # Add deployment method detection
        data['deployment_method'] = cls.detect_deployment_method()

        return ProxyConfig(**data)