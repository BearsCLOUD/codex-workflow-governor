#!/usr/bin/env python3
"""Authorize, revoke, or list Git roots for the local Workflow Governor MCP."""

from pathlib import Path
import runpy
import sys


if __name__ == "__main__":
    server = Path(__file__).resolve().parents[1] / "mcp" / "server.py"
    sys.argv = [str(server), "roots", *sys.argv[1:]]
    runpy.run_path(str(server), run_name="__main__")
