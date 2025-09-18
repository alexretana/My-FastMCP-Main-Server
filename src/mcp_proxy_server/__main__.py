"""
Entry point for uvx direct execution.

This module allows the package to be executed directly with:
    uvx run mcp-proxy-server

or with Python's -m flag:
    python -m mcp_proxy_server
"""

import sys
from .cli import main

if __name__ == "__main__":
    sys.exit(main())