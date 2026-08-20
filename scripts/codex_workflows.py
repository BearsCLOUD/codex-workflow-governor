#!/usr/bin/env python3
"""Executable entry point for the self-contained Codex Workflows skill."""

import runpy
from pathlib import Path


if __name__ == "__main__":
    script = Path(__file__).resolve().parents[1] / "skills" / "codex-workflows" / "scripts" / "codex_workflows.py"
    runpy.run_path(str(script), run_name="__main__")
