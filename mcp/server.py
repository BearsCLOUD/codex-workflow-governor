#!/usr/bin/env python3
"""Public stdio MCP facade for the local workflow tools.

Protocol, validation, CLI bridging, and dispatch live in :mod:`mcp.runtime`;
this file intentionally remains the stable executable/module entry point.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__:
    from . import runtime as _runtime
else:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from mcp import runtime as _runtime

for _name, _value in vars(_runtime).items():
    if not _name.startswith("__"):
        globals()[_name] = _value
main = _runtime.main

# Imported callers historically patched helpers through ``mcp.server``.  Keep
# that module identity bound to the implementation so protocol/dispatch
# helpers and their tests share one function-global namespace.
if __package__:
    sys.modules[__name__] = _runtime


if __name__ == "__main__":
    raise SystemExit(main())
