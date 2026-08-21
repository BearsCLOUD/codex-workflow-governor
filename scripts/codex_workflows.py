#!/usr/bin/env python3
"""Package-relative launcher for the self-contained Codex Workflows skill."""

from __future__ import annotations

import sys
from pathlib import Path


_SKILL_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "codex-workflows" / "scripts"
if str(_SKILL_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SKILL_SCRIPTS))

from workflow_runtime.api import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
