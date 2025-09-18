"""
Test package structure and basic imports for Phase 0.
"""

import pytest
import sys
from pathlib import Path

# Add src to path for testing
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


def test_package_imports():
    """Test that all main package components can be imported."""
    # Test main package import
    import mcp_proxy_server
    assert mcp_proxy_server.__version__ == "0.1.0"

    # Test main components
    from mcp_proxy_server import ProxyConfig, ServerConfig, CredentialManager
    from mcp_proxy_server.cli import main
    from mcp_proxy_server.config import ConfigLoader, DeploymentMethod
    from mcp_proxy_server.credentials import CredentialManager
    from mcp_proxy_server.proxy import MCPProxyServer

    assert ProxyConfig is not None
    assert ServerConfig is not None
    assert CredentialManager is not None
    assert main is not None
    assert ConfigLoader is not None
    assert DeploymentMethod is not None
    assert MCPProxyServer is not None


def test_deployment_method_detection():
    """Test deployment method detection."""
    from mcp_proxy_server.config import ConfigLoader

    # Should not raise an exception
    deployment_method = ConfigLoader.detect_deployment_method()
    assert deployment_method is not None


def test_credential_manager_initialization():
    """Test credential manager can be initialized."""
    from mcp_proxy_server.credentials import CredentialManager

    # Should initialize without error
    cred_manager = CredentialManager()
    assert cred_manager is not None

    # Should be able to get credential paths
    paths = cred_manager.get_credential_paths()
    assert isinstance(paths, list)


def test_config_validation():
    """Test basic configuration validation."""
    from mcp_proxy_server.config import ProxyConfig, ServerConfig
    from pydantic import ValidationError

    # Valid minimal config
    server_config = ServerConfig(
        name="test-server",
        command=["echo", "test"],
        transport="stdio"
    )

    config = ProxyConfig(
        servers=[server_config]
    )

    assert config.host == "localhost"
    assert config.port == 8080
    assert len(config.servers) == 1

    # Invalid config (no servers)
    with pytest.raises(ValidationError):
        ProxyConfig(servers=[])


def test_cli_entry_point():
    """Test that CLI can be imported and initialized."""
    from mcp_proxy_server.cli import cli

    # Should be a click group
    assert hasattr(cli, 'commands')
    assert 'run' in cli.commands
    assert 'validate' in cli.commands
    assert 'create-config' in cli.commands
    assert 'status' in cli.commands


if __name__ == "__main__":
    pytest.main([__file__])