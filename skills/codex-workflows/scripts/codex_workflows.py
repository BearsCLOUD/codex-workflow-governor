#!/usr/bin/env python3
"""Public executable facade for the self-contained Codex Workflows skill."""

from __future__ import annotations

from workflow_runtime.api import main


if __name__ == "__main__":
    raise SystemExit(main())
