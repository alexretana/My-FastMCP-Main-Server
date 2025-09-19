"""
Command-line interface for MCP Proxy Server.

This module provides the main CLI entry point with support for various commands
and deployment methods (uv install, uvx run, Docker).
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Optional

import click
from pydantic import ValidationError
from dotenv import load_dotenv

from . import __version__
from .config import ProxyConfig, ConfigLoader, DeploymentMethod
from .credentials import CredentialManager
from .proxy import FastMCPProxyServer


def setup_logging(level: str, log_file: Optional[str] = None) -> None:
    """Set up logging configuration."""
    log_level = getattr(logging, level.upper(), logging.INFO)

    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Set up root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)


@click.group()
@click.version_option(version=__version__)
@click.option('--verbose', '-v', is_flag=True, help='Enable verbose logging')
@click.option('--debug', is_flag=True, help='Enable debug logging')
@click.option('--log-file', type=click.Path(), help='Log file path')
@click.pass_context
def cli(ctx, verbose: bool, debug: bool, log_file: Optional[str]) -> None:
    """MCP Proxy Server - Aggregate multiple MCP servers."""
    # Load environment variables from .env file
    load_dotenv()

    # Ensure context object exists
    ctx.ensure_object(dict)

    # Determine log level
    if debug:
        log_level = 'DEBUG'
    elif verbose:
        log_level = 'INFO'
    else:
        log_level = 'WARNING'

    # Set up logging
    setup_logging(log_level, log_file)
    ctx.obj['log_level'] = log_level


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True, path_type=Path),
    help='Configuration file path (JSON or YAML)'
)
@click.option(
    '--host',
    default=None,
    help='Host to bind to (overrides config)'
)
@click.option(
    '--port', '-p',
    type=int,
    default=None,
    help='Port to bind to (overrides config)'
)
@click.option(
    '--daemon', '-d',
    is_flag=True,
    help='Run in daemon mode'
)
@click.pass_context
def run(ctx, config: Optional[Path], host: Optional[str], port: Optional[int], daemon: bool) -> None:
    """Run the MCP proxy server."""
    try:
        # Load configuration
        if config:
            proxy_config = ConfigLoader.load_from_file(config)
            click.echo(f"Loaded configuration from {config}")
        else:
            # Look for default configuration files
            default_paths = [
                Path.cwd() / "mcp-proxy.json",
                Path.cwd() / "mcp-proxy.yaml",
                Path.cwd() / "config.json",
                Path.cwd() / "config.yaml",
            ]

            config_found = None
            for default_path in default_paths:
                if default_path.exists():
                    config_found = default_path
                    break

            if config_found:
                proxy_config = ConfigLoader.load_from_file(config_found)
                click.echo(f"Using default configuration: {config_found}")
            else:
                click.echo("No configuration file found. Use --config or create mcp-proxy.json")
                click.echo("Run 'mcp-proxy create-config' to generate a template.")
                sys.exit(1)

        # Apply CLI overrides
        if host:
            proxy_config.host = host
        if port:
            proxy_config.port = port

        # Load credentials
        credential_manager = CredentialManager(proxy_config.deployment_method)
        credentials = credential_manager.load_all_credentials()

        # Validate credentials
        credential_issues = credential_manager.validate_credentials()
        if credential_issues:
            click.echo("Credential validation warnings:")
            for key, issue in credential_issues.items():
                click.echo(f"  - {key}: {issue}")

        # Display deployment information
        if ctx.obj.get('log_level') in ('DEBUG', 'INFO'):
            deployment_info = credential_manager.get_deployment_info()
            click.echo(f"Deployment method: {deployment_info['deployment_method']}")

        # Create and start proxy server
        proxy_server = FastMCPProxyServer(proxy_config, credentials)

        click.echo(f"Starting MCP proxy server on {proxy_config.host}:{proxy_config.port}")
        click.echo(f"Transport: {proxy_config.transport}")
        click.echo(f"Configured servers: {len(proxy_config.servers)}")

        if daemon:
            click.echo("Running in daemon mode...")
            # TODO: Implement daemon mode
            proxy_server.run_daemon()
        else:
            proxy_server.run()

    except ValidationError as e:
        click.echo(f"Configuration validation error: {e}", err=True)
        sys.exit(1)
    except FileNotFoundError as e:
        click.echo(f"File not found: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"Error starting server: {e}", err=True)
        if ctx.obj.get('log_level') == 'DEBUG':
            import traceback
            traceback.print_exc()
        sys.exit(1)


@cli.command()
@click.option(
    '--config', '-c',
    type=click.Path(exists=True, path_type=Path),
    help='Configuration file path to validate'
)
@click.option(
    '--check-credentials',
    is_flag=True,
    help='Also validate credential configuration'
)
@click.pass_context
def validate(ctx, config: Optional[Path], check_credentials: bool) -> None:
    """Validate configuration file and credentials."""
    try:
        if not config:
            click.echo("Please specify a configuration file with --config", err=True)
            sys.exit(1)

        # Load and validate configuration
        proxy_config = ConfigLoader.load_from_file(config)
        click.echo(f"[OK] Configuration file '{config}' is valid")

        # Display configuration summary
        click.echo(f"  - Servers configured: {len(proxy_config.servers)}")
        click.echo(f"  - Transport: {proxy_config.transport}")
        click.echo(f"  - Host: {proxy_config.host}:{proxy_config.port}")
        click.echo(f"  - Deployment method: {proxy_config.deployment_method}")

        if check_credentials:
            # Validate credentials
            credential_manager = CredentialManager(proxy_config.deployment_method)
            credentials = credential_manager.load_all_credentials()
            credential_issues = credential_manager.validate_credentials()

            if credential_issues:
                click.echo("\n[WARNING] Credential validation warnings:")
                for key, issue in credential_issues.items():
                    click.echo(f"  - {key}: {issue}")
            else:
                click.echo("[OK] Credentials are valid")

            # Show credential sources
            if ctx.obj.get('log_level') in ('DEBUG', 'INFO'):
                click.echo("\nCredential sources:")
                for key, source in credential_manager.credential_sources.items():
                    click.echo(f"  - {key}: {source.source_type}")

    except ValidationError as e:
        click.echo(f"[ERROR] Configuration validation failed: {e}", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"[ERROR] Validation error: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option(
    '--output', '-o',
    type=click.Path(path_type=Path),
    default="mcp-proxy.json",
    help='Output file path'
)
@click.option(
    '--deployment',
    type=click.Choice(['uv', 'uvx', 'docker', 'manual']),
    help='Target deployment method'
)
@click.option(
    '--with-credentials',
    is_flag=True,
    help='Also create a credential template'
)
def create_config(output: Path, deployment: Optional[str], with_credentials: bool) -> None:
    """Create a configuration template."""
    # Map CLI values to deployment methods
    deployment_map = {
        'uv': DeploymentMethod.UV_INSTALL,
        'uvx': DeploymentMethod.UVX_RUN,
        'docker': DeploymentMethod.DOCKER,
        'manual': DeploymentMethod.MANUAL,
    }

    target_deployment = deployment_map.get(deployment) if deployment else ConfigLoader.detect_deployment_method()

    # Create basic configuration template
    config_template = {
        "host": "localhost",
        "port": 8080,
        "transport": "stdio",
        "log_level": "INFO",
        "servers": [
            {
                "name": "example-server",
                "transport": "stdio",
                "command": ["python", "-m", "example_mcp_server"],
                "enabled": True,
                "timeout": 30,
                "namespace": "example"
            }
        ],
        "auth": {
            "enabled": False
        },
        "monitoring": {
            "enabled": True,
            "health_check_enabled": True
        }
    }

    # Deployment-specific adjustments
    if target_deployment == DeploymentMethod.DOCKER:
        config_template["host"] = "0.0.0.0"
        config_template["log_file"] = "/var/log/mcp-proxy.log"

    elif target_deployment == DeploymentMethod.UVX_RUN:
        config_template["log_file"] = "./mcp-proxy.log"

    # Write configuration
    with open(output, 'w', encoding='utf-8') as f:
        json.dump(config_template, f, indent=2)

    click.echo(f"Created configuration template: {output}")
    click.echo(f"Target deployment: {target_deployment}")

    # Create credential template if requested
    if with_credentials:
        cred_path = output.parent / "credentials.json"
        credential_manager = CredentialManager(target_deployment)
        credential_manager.create_credential_template(cred_path)
        click.echo(f"Created credential template: {cred_path}")

    click.echo("\nNext steps:")
    click.echo(f"1. Edit {output} to configure your MCP servers")
    if with_credentials:
        click.echo(f"2. Edit {cred_path} with your credentials")
    click.echo(f"3. Run: mcp-proxy run --config {output}")


@cli.command()
@click.option(
    '--output', '-o',
    type=click.Choice(['json', 'yaml']),
    default='json',
    help='Output format'
)
def status(output: str) -> None:
    """Show proxy server status and deployment information."""
    try:
        # Detect deployment method
        deployment_method = ConfigLoader.detect_deployment_method()
        credential_manager = CredentialManager(deployment_method)

        # Get deployment information
        deployment_info = credential_manager.get_deployment_info()

        status_data = {
            "deployment_method": deployment_info['deployment_method'],
            "credential_paths_checked": deployment_info['credential_paths_checked'],
            "environment_variables": {
                var: os.environ.get(var, 'not set')
                for var in deployment_info['environment_variables_checked']
            },
            "package_info": {
                "version": __version__,
                "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
                "platform": sys.platform
            }
        }

        if output == 'json':
            click.echo(json.dumps(status_data, indent=2))
        else:
            # YAML output
            import yaml
            click.echo(yaml.dump(status_data, default_flow_style=False))

    except Exception as e:
        click.echo(f"Error getting status: {e}", err=True)
        sys.exit(1)


@cli.command()
def version() -> None:
    """Show version information."""
    deployment_method = ConfigLoader.detect_deployment_method()
    click.echo(f"MCP Proxy Server {__version__}")
    click.echo(f"Deployment method: {deployment_method}")
    click.echo(f"Python {sys.version}")


def main() -> int:
    """Main entry point for CLI."""
    try:
        cli()
        return 0
    except KeyboardInterrupt:
        click.echo("\nInterrupted by user", err=True)
        return 1
    except Exception as e:
        click.echo(f"Unexpected error: {e}", err=True)
        return 1


if __name__ == '__main__':
    sys.exit(main())