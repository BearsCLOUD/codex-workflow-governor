#!/usr/bin/env python3
"""Run a Workflow Governor MCP stdio surface."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_governor.mcp_server import main


if __name__ == "__main__":
    raise SystemExit(main())

