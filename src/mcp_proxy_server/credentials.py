"""
Credential management for MCP Proxy Server.

This module provides credential discovery and loading with support for multiple
deployment methods and secure credential handling.
"""

import os
import json
from pathlib import Path
from typing import Dict, Optional, Any, Union
from dataclasses import dataclass
import logging

from .config import DeploymentMethod, ConfigLoader

logger = logging.getLogger(__name__)


@dataclass
class CredentialSource:
    """Represents a credential source with metadata."""
    source_type: str  # 'file', 'env', 'default'
    source_path: Optional[str] = None
    is_secure: bool = True
    priority: int = 0  # Higher = more priority


class CredentialManager:
    """
    Manages credentials for MCP Proxy Server with deployment-aware discovery.

    Supports multiple credential sources with precedence:
    1. Environment variables (highest priority)
    2. Deployment-specific credential files
    3. Default credential locations
    4. Built-in defaults (lowest priority)
    """

    def __init__(self, deployment_method: Optional[DeploymentMethod] = None):
        """Initialize credential manager with deployment method detection."""
        self.deployment_method = deployment_method or ConfigLoader.detect_deployment_method()
        self.credentials: Dict[str, Any] = {}
        self.credential_sources: Dict[str, CredentialSource] = {}

    def get_credential_paths(self) -> list[Path]:
        """Get ordered list of credential file paths based on deployment method."""
        paths = []

        # Common paths for all deployment methods
        home = Path.home()
        config_dirs = [
            home / ".config" / "mcp-proxy",
            home / ".mcp-proxy",
        ]

        # Add deployment-specific paths
        if self.deployment_method == DeploymentMethod.UVX_RUN:
            # For uvx, check temporary locations and current directory
            if 'UV_TOOL_DIR' in os.environ:
                uv_tool_dir = Path(os.environ['UV_TOOL_DIR'])
                config_dirs.insert(0, uv_tool_dir / "mcp-proxy")

            # Check current working directory for uvx runs
            config_dirs.insert(0, Path.cwd() / ".mcp-proxy")

        elif self.deployment_method == DeploymentMethod.UV_INSTALL:
            # For uv install, prefer standard config locations
            if 'VIRTUAL_ENV' in os.environ:
                venv_path = Path(os.environ['VIRTUAL_ENV'])
                config_dirs.insert(0, venv_path / "mcp-proxy")

        elif self.deployment_method == DeploymentMethod.DOCKER:
            # For Docker, check mounted volumes and container paths
            docker_paths = [
                Path("/etc/mcp-proxy"),
                Path("/config"),
                Path("/app/config"),
            ]
            config_dirs = docker_paths + config_dirs

        # Create full paths for credential files
        for config_dir in config_dirs:
            for filename in ["credentials.json", "config.json", ".credentials"]:
                paths.append(config_dir / filename)

        return paths

    def load_credentials_from_file(self, file_path: Path) -> Optional[Dict[str, Any]]:
        """Load credentials from a JSON file."""
        try:
            if not file_path.exists():
                return None

            with open(file_path, 'r', encoding='utf-8') as f:
                data = json.load(f)

            logger.info(f"Loaded credentials from {file_path}")
            return data

        except (json.JSONDecodeError, IOError) as e:
            logger.warning(f"Failed to load credentials from {file_path}: {e}")
            return None

    def load_credentials_from_env(self) -> Dict[str, Any]:
        """Load credentials from environment variables."""
        credentials = {}

        # Define environment variable mappings
        env_mappings = {
            'jwt_secret': ['MCP_PROXY_JWT_SECRET', 'JWT_SECRET'],
            'api_keys': ['MCP_PROXY_API_KEYS', 'API_KEYS'],
            'database_url': ['MCP_PROXY_DATABASE_URL', 'DATABASE_URL'],
            'redis_url': ['MCP_PROXY_REDIS_URL', 'REDIS_URL'],
            'prometheus_token': ['MCP_PROXY_PROMETHEUS_TOKEN', 'PROMETHEUS_TOKEN'],
            'github_token': ['MCP_PROXY_GITHUB_TOKEN', 'GITHUB_TOKEN'],
            'openai_api_key': ['MCP_PROXY_OPENAI_API_KEY', 'OPENAI_API_KEY'],
            'anthropic_api_key': ['MCP_PROXY_ANTHROPIC_API_KEY', 'ANTHROPIC_API_KEY'],
        }

        for credential_key, env_vars in env_mappings.items():
            for env_var in env_vars:
                value = os.environ.get(env_var)
                if value:
                    # Special handling for list values
                    if credential_key == 'api_keys':
                        credentials[credential_key] = [
                            key.strip() for key in value.split(',') if key.strip()
                        ]
                    else:
                        credentials[credential_key] = value

                    self.credential_sources[credential_key] = CredentialSource(
                        source_type="env",
                        source_path=env_var,
                        is_secure=True,
                        priority=100
                    )
                    break

        if credentials:
            logger.info(f"Loaded {len(credentials)} credentials from environment variables")

        return credentials

    def get_default_credentials(self) -> Dict[str, Any]:
        """Get default credentials for development."""
        defaults = {
            'jwt_secret': 'dev-secret-change-in-production',
            'api_keys': [],
        }

        for key in defaults:
            self.credential_sources[key] = CredentialSource(
                source_type="default",
                is_secure=False,
                priority=0
            )

        return defaults

    def load_all_credentials(self) -> Dict[str, Any]:
        """Load credentials from all sources with proper precedence."""
        all_credentials = {}

        # 1. Start with defaults (lowest priority)
        defaults = self.get_default_credentials()
        all_credentials.update(defaults)

        # 2. Load from files (medium priority)
        credential_paths = self.get_credential_paths()
        for path in credential_paths:
            file_creds = self.load_credentials_from_file(path)
            if file_creds:
                all_credentials.update(file_creds)
                # Update sources for file credentials
                for key in file_creds:
                    self.credential_sources[key] = CredentialSource(
                        source_type="file",
                        source_path=str(path),
                        is_secure=True,
                        priority=50
                    )
                break  # Use first found file

        # 3. Load from environment (highest priority)
        env_creds = self.load_credentials_from_env()
        all_credentials.update(env_creds)

        self.credentials = all_credentials
        return all_credentials

    def get_credential(self, key: str, default: Any = None) -> Any:
        """Get a specific credential value."""
        return self.credentials.get(key, default)

    def has_credential(self, key: str) -> bool:
        """Check if a credential exists."""
        return key in self.credentials

    def get_credential_source(self, key: str) -> Optional[CredentialSource]:
        """Get the source information for a credential."""
        return self.credential_sources.get(key)

    def validate_credentials(self) -> Dict[str, str]:
        """Validate credentials and return any issues."""
        issues = {}

        # Check for insecure defaults in production-like environments
        if self.deployment_method in (DeploymentMethod.DOCKER, DeploymentMethod.UV_INSTALL):
            jwt_secret = self.get_credential('jwt_secret')
            if jwt_secret == 'dev-secret-change-in-production':
                issues['jwt_secret'] = "Using default JWT secret in production environment"

        # Check for missing required credentials
        required_for_auth = ['jwt_secret']
        auth_enabled = self.get_credential('auth_enabled', False)
        if auth_enabled:
            for req_cred in required_for_auth:
                if not self.has_credential(req_cred):
                    issues[req_cred] = f"Required credential '{req_cred}' not found"

        return issues

    def create_credential_template(self, output_path: Union[str, Path]) -> None:
        """Create a credential template file."""
        template = {
            "_comment": "MCP Proxy Server Credentials",
            "_note": "This file contains sensitive information. Keep it secure!",
            "jwt_secret": "your-secure-jwt-secret-here",
            "api_keys": [
                "your-api-key-1",
                "your-api-key-2"
            ],
            "github_token": "your-github-token",
            "openai_api_key": "your-openai-api-key",
            "anthropic_api_key": "your-anthropic-api-key",
            "database_url": "postgresql://user:pass@host:port/db",
            "redis_url": "redis://localhost:6379/0",
            "oauth": {
                "github": {
                    "client_id": "your-github-client-id",
                    "client_secret": "your-github-client-secret"
                },
                "google": {
                    "client_id": "your-google-client-id",
                    "client_secret": "your-google-client-secret"
                }
            }
        }

        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(template, f, indent=2)

        logger.info(f"Created credential template at {output_path}")

    def get_deployment_info(self) -> Dict[str, Any]:
        """Get information about the current deployment."""
        return {
            'deployment_method': self.deployment_method,
            'credential_paths_checked': [str(p) for p in self.get_credential_paths()],
            'credential_sources': {
                key: {
                    'type': source.source_type,
                    'path': source.source_path,
                    'secure': source.is_secure,
                    'priority': source.priority
                }
                for key, source in self.credential_sources.items()
            },
            'environment_variables_checked': [
                'MCP_PROXY_JWT_SECRET', 'JWT_SECRET',
                'MCP_PROXY_API_KEYS', 'API_KEYS',
                'UV_TOOL_DIR', 'VIRTUAL_ENV', 'DOCKER_CONTAINER'
            ]
        }