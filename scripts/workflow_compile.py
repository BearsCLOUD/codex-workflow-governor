#!/usr/bin/env python3
"""Compile or verify a repository workflow without an MCP client."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from workflow_governor.compiler import compile_file, verify_generated_views, write_generated_views
from workflow_governor.contracts import ContractError, workflow_directory


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("repository", type=Path)
    parser.add_argument("workflow_id")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    repository = args.repository.resolve()
    try:
        source_path = workflow_directory(repository, args.workflow_id) / "workflow.json"
        if args.check:
            errors = verify_generated_views(repository, args.workflow_id)
            print(json.dumps({"ok": not errors, "errors": errors}, sort_keys=True))
            return 0 if not errors else 1
        lock = compile_file(repository, source_path)
        write_generated_views(repository, args.workflow_id, lock)
        print(json.dumps({"workflow_id": args.workflow_id, "lock_digest": lock["lock_digest"]}, sort_keys=True))
        return 0
    except (ContractError, OSError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, sort_keys=True), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
