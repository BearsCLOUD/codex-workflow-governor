from __future__ import annotations

import json
import os
import stat
import subprocess
import sys
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

PLUGIN_SCRIPTS = Path(__file__).resolve().parents[1] / "skills" / "codex-workflows" / "scripts"
if str(PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(PLUGIN_SCRIPTS))

from mcp import redaction, root_auth  # noqa: E402
from mcp.server import (  # noqa: E402
    DEFAULT_PROTOCOL_VERSION,
    MAX_RESPONSE_BYTES,
    RESULT_SCHEMA,
    TOOL_NAMES,
    WorkflowMcpServer,
    _sanitize,
)


PLUGIN_ROOT = Path(__file__).resolve().parents[1]


def _git(project: Path) -> None:
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.name", "MCP Test"], check=True)
    subprocess.run(["git", "-C", str(project), "config", "user.email", "mcp@example.invalid"], check=True)
    (project / "README.md").write_text("fixture\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(project), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(project), "commit", "-qm", "fixture"], check=True)


def _fake_codex(path: Path) -> None:
    path.write_text(
        """#!/usr/bin/env python3
import json
import sys
from pathlib import Path

def option(name):
    return sys.argv[sys.argv.index(name) + 1]

schema = json.loads(Path(option('--output-schema')).read_text(encoding='utf-8'))

def value(spec):
    kind = spec.get('type')
    if 'enum' in spec:
        return spec['enum'][0]
    if kind == 'object':
        return {name: value(child) for name, child in spec.get('properties', {}).items()}
    if kind == 'array':
        return []
    if kind == 'string':
        return 'ok'
    if kind == 'integer':
        return 1
    if kind == 'number':
        return 1.0
    if kind == 'boolean':
        return True
    return None

Path(option('--output-last-message')).write_text(json.dumps(value(schema)), encoding='utf-8')
print(json.dumps({'type': 'turn.completed'}))
""",
        encoding="utf-8",
    )
    path.chmod(0o700)


class LocalMcpTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.home = self.base / "home"
        self.codex_home = self.base / "codex"
        self.project = self.base / "project"
        self.home.mkdir(mode=0o700)
        self.codex_home.mkdir(mode=0o700)
        self.project.mkdir(mode=0o700)
        _git(self.project)
        self.environment = patch.dict(
            os.environ,
            {"HOME": str(self.home), "CODEX_HOME": str(self.codex_home)},
            clear=False,
        )
        self.environment.start()
        root_auth.authorize(self.project)

    def tearDown(self) -> None:
        self.environment.stop()
        self.temporary.cleanup()

    def server(self) -> WorkflowMcpServer:
        server = WorkflowMcpServer()
        fake = self.base / "fake-codex"
        _fake_codex(fake)
        server.codex = fake
        return server

    def direct_run(
        self,
        request_id: str,
        *,
        failpoint: str | None = None,
        workflow: str = "builtin:fanout-synthesize",
        input_values: dict[str, object] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        fake = self.base / "direct-fake-codex"
        _fake_codex(fake)
        inputs = self.base / "direct-inputs.json"
        inputs.write_text(
            json.dumps(
                input_values or {"items": [1], "request": "summarize"},
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        identity = root_auth.identity_digest(root_auth.project_identity(self.project))
        environment = dict(os.environ)
        if failpoint is not None:
            environment["CODEX_WORKFLOWS_MCP_TEST_FAILPOINT"] = failpoint
        else:
            environment.pop("CODEX_WORKFLOWS_MCP_TEST_FAILPOINT", None)
        return subprocess.run(
            [
                sys.executable,
                str(PLUGIN_ROOT / "scripts" / "codex_workflows.py"),
                "--project-root",
                str(self.project),
                "--mcp-expected-root-identity",
                identity,
                "--mcp-qualified-only",
                "run",
                workflow,
                "--inputs",
                str(inputs),
                "--codex-bin",
                str(fake),
                "--detach",
                "--mcp-json",
                "--mcp-request-id",
                request_id,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            text=True,
            check=False,
        )

    def direct_control(
        self,
        run_id: str,
        request_id: str,
        action: str,
        *,
        failpoint: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        identity = root_auth.identity_digest(root_auth.project_identity(self.project))
        environment = dict(os.environ)
        if failpoint is not None:
            environment["CODEX_WORKFLOWS_MCP_TEST_FAILPOINT"] = failpoint
        else:
            environment.pop("CODEX_WORKFLOWS_MCP_TEST_FAILPOINT", None)
        return subprocess.run(
            [
                sys.executable,
                str(PLUGIN_ROOT / "scripts" / "codex_workflows.py"),
                "--project-root",
                str(self.project),
                "--mcp-expected-root-identity",
                identity,
                action,
                run_id,
                "--mcp-json",
                "--mcp-request-id",
                request_id,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environment,
            text=True,
            check=False,
        )

    def test_protocol_exposes_exact_four_tools_with_mutation_annotations(self) -> None:
        server = self.server()
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})
        self.assertIsNotNone(response)
        tools = {item["name"]: item for item in response["result"]["tools"]}
        self.assertEqual(set(tools), TOOL_NAMES)
        self.assertTrue(tools["workflow_plan"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["workflow_status"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["workflow_run"]["annotations"]["destructiveHint"])
        self.assertTrue(tools["workflow_control"]["annotations"]["destructiveHint"])

    def test_plan_is_qualified_only_and_colon_path_cannot_shadow_builtin(self) -> None:
        (self.project / "builtin:fanout-synthesize").write_text("shadow\n", encoding="utf-8")
        result, is_error = self.server().call_tool(
            "workflow_plan",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "inputs": {"request": "summarize", "items": [1, 2]},
            },
        )
        self.assertFalse(is_error, result)
        self.assertEqual(result["schema_version"], RESULT_SCHEMA)
        self.assertEqual(result["data"]["workflow_scope"], "builtin")
        self.assertEqual(result["data"]["task_order"], ["analyze-items", "synthesize"])

    def test_unauthorized_root_and_invalid_mutation_id_fail_closed(self) -> None:
        other = self.base / "other"
        other.mkdir()
        _git(other)
        result, is_error = self.server().call_tool(
            "workflow_plan",
            {"project_root": str(other), "workflow": "builtin:fanout-synthesize"},
        )
        self.assertTrue(is_error)
        self.assertEqual(result["error"]["code"], "unauthorized_root")
        invalid, invalid_error = self.server().call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "request_id": str(uuid.uuid1()),
            },
        )
        self.assertTrue(invalid_error)
        self.assertEqual(invalid["error"]["code"], "invalid_arguments")

    def test_detached_run_retry_and_status_use_one_caller_request(self) -> None:
        server = self.server()
        request_id = str(uuid.uuid4())
        arguments = {
            "project_root": str(self.project),
            "workflow": "builtin:fanout-synthesize",
            "request_id": request_id,
            "inputs": {"request": "summarize", "items": [1]},
        }
        first, first_error = server.call_tool("workflow_run", arguments)
        self.assertFalse(first_error, first)
        run_id = first["data"]["run_id"]
        repeated, repeated_error = server.call_tool("workflow_run", arguments)
        self.assertFalse(repeated_error, repeated)
        self.assertEqual(repeated["data"]["run_id"], run_id)

        deadline = time.monotonic() + 15
        status = None
        while time.monotonic() < deadline:
            status, status_error = server.call_tool(
                "workflow_status",
                {"project_root": str(self.project), "request_id": request_id},
            )
            self.assertFalse(status_error, status)
            if status["data"]["terminal"]:
                break
            time.sleep(0.05)
        self.assertIsNotNone(status)
        self.assertEqual(status["data"]["run_id"], run_id)
        self.assertEqual(status["data"]["observed_status"], "completed")
        self.assertNotIn("inputs", status["data"])
        self.assertNotIn("project_root", status["data"])
        self.assertTrue(status["data"]["result"]["available"])

        changed = dict(arguments)
        changed["inputs"] = {"request": "different", "items": [1]}
        mismatch, mismatch_error = server.call_tool("workflow_run", changed)
        self.assertTrue(mismatch_error)
        self.assertEqual(mismatch["error"]["code"], "cli_error")

    def test_run_and_control_share_one_request_id_namespace(self) -> None:
        server = self.server()
        request_id = str(uuid.uuid4())
        run, run_error = server.call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "request_id": request_id,
                "inputs": {"request": "summarize", "items": [1]},
            },
        )
        self.assertFalse(run_error, run)

        implementation = sys.modules["workflow_runtime.engine"]
        transaction = implementation._mutation_lookup(self.project, request_id)
        self.assertIsNotNone(transaction)
        self.assertEqual(transaction["operation"], "run")
        ledger = implementation._mutation_database_path(self.project)
        self.assertEqual(stat.S_IMODE(ledger.stat().st_mode), 0o600)

        reused, reused_error = server.call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run["data"]["run_id"],
                "request_id": request_id,
                "action": "cancel",
            },
        )
        self.assertTrue(reused_error)
        self.assertEqual(reused["error"]["code"], "cli_error")
        self.assertIn("different MCP mutation operation", reused["error"]["message"])

        control_id = str(uuid.uuid4())
        controlled, controlled_error = server.call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run["data"]["run_id"],
                "request_id": control_id,
                "action": "cancel",
            },
        )
        self.assertFalse(controlled_error, controlled)
        reverse, reverse_error = server.call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "request_id": control_id,
                "inputs": {"request": "summarize", "items": [1]},
            },
        )
        self.assertTrue(reverse_error)
        self.assertEqual(reverse["error"]["code"], "cli_error")
        self.assertIn("different MCP mutation operation", reverse["error"]["message"])

    def test_concurrent_identical_and_mismatched_requests_are_serialized_by_sqlite(self) -> None:
        server = self.server()
        request_id = str(uuid.uuid4())
        arguments = {
            "project_root": str(self.project),
            "workflow": "builtin:fanout-synthesize",
            "request_id": request_id,
            "inputs": {"request": "summarize", "items": [1]},
        }
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: server.call_tool("workflow_run", arguments), range(2)))
        self.assertTrue(all(not is_error for _, is_error in results), results)
        self.assertEqual(len({result["data"]["run_id"] for result, _ in results}), 1)

        mismatched_id = str(uuid.uuid4())
        requests = [
            {
                **arguments,
                "request_id": mismatched_id,
                "inputs": {"request": value, "items": [1]},
            }
            for value in ("first", "second")
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            mismatched = list(executor.map(lambda item: server.call_tool("workflow_run", item), requests))
        self.assertEqual(sum(not is_error for _, is_error in mismatched), 1, mismatched)
        self.assertEqual(sum(is_error for _, is_error in mismatched), 1, mismatched)
        loser = next(result for result, is_error in mismatched if is_error)
        self.assertIn("different run arguments", loser["error"]["message"])

    def test_distinct_requests_cannot_publish_the_same_persistent_instance(self) -> None:
        server = self.server()
        requests = [
            {
                "project_root": str(self.project),
                "workflow": "builtin:loop-monitor",
                "request_id": str(uuid.uuid4()),
                "inputs": {"cursor": "initial", "instance": "one-owner", "request": "monitor"},
            }
            for _ in range(2)
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda item: server.call_tool("workflow_run", item), requests))
        self.assertEqual(sum(not is_error for _, is_error in results), 1, results)
        self.assertEqual(sum(is_error for _, is_error in results), 1, results)
        rejected = next(result for result, is_error in results if is_error)
        self.assertIn("active loop", rejected["error"]["message"])

        started = next(result for result, is_error in results if not is_error)
        run_id = started["data"]["run_id"]
        implementation = sys.modules["workflow_runtime.engine"]
        published = [
            path
            for path in implementation._runs_root(self.project).glob("exec_*")
            if (path / "run.json").is_file()
        ]
        self.assertEqual([path.name for path in published], [run_id])
        cancelled, cancel_error = server.call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run_id,
                "request_id": str(uuid.uuid4()),
                "action": "cancel",
            },
        )
        self.assertFalse(cancel_error, cancelled)

    def test_persisted_instance_claim_survives_prepublication_crash(self) -> None:
        original_request = str(uuid.uuid4())
        input_values = {"cursor": "initial", "instance": "claim-crash", "request": "monitor"}
        crashed = self.direct_run(
            original_request,
            failpoint="after-loop-instance-claim",
            workflow="builtin:loop-monitor",
            input_values=input_values,
        )
        self.assertEqual(crashed.returncode, 86, crashed.stderr)
        implementation = sys.modules["workflow_runtime.engine"]
        original = implementation._mutation_lookup(self.project, original_request)
        self.assertEqual(original["phase"], "bound")
        self.assertFalse(
            (implementation._runs_root(self.project) / original["run_id"] / "run.json").exists()
        )

        competing, competing_error = self.server().call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:loop-monitor",
                "request_id": str(uuid.uuid4()),
                "inputs": input_values,
            },
        )
        self.assertTrue(competing_error, competing)
        self.assertIn("still publishing", competing["error"]["message"])

        recovered = self.direct_run(
            original_request,
            workflow="builtin:loop-monitor",
            input_values=input_values,
        )
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        run_id = json.loads(recovered.stdout)["run_id"]
        self.assertEqual(run_id, original["run_id"])
        published = [
            path
            for path in implementation._runs_root(self.project).glob("exec_*")
            if (path / "run.json").is_file()
        ]
        self.assertEqual([path.name for path in published], [run_id])
        cancelled, cancel_error = self.server().call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run_id,
                "request_id": str(uuid.uuid4()),
                "action": "cancel",
            },
        )
        self.assertFalse(cancel_error, cancelled)

    def test_run_recovery_reuses_reserved_run_after_process_crash(self) -> None:
        request_id = str(uuid.uuid4())
        crashed = self.direct_run(request_id, failpoint="after-run-prepared")
        self.assertEqual(crashed.returncode, 86, crashed.stderr)
        implementation = sys.modules["workflow_runtime.engine"]
        transaction = implementation._mutation_lookup(self.project, request_id)
        self.assertEqual(transaction["phase"], "bound")
        reserved_run_id = transaction["run_id"]
        self.assertTrue((implementation._runs_root(self.project) / reserved_run_id / "run.json").is_file())

        recovered = self.direct_run(request_id)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["run_id"], reserved_run_id)
        transaction = implementation._mutation_lookup(self.project, request_id)
        self.assertEqual(transaction["phase"], "acknowledged")
        run_directories = [
            path
            for path in implementation._runs_root(self.project).glob("exec_*")
            if path.is_dir()
        ]
        self.assertEqual([path.name for path in run_directories], [reserved_run_id])

    def test_reservation_only_status_is_read_only_and_retryable(self) -> None:
        request_id = str(uuid.uuid4())
        crashed = self.direct_run(request_id, failpoint="after-request-reserved")
        self.assertEqual(crashed.returncode, 86, crashed.stderr)
        implementation = sys.modules["workflow_runtime.engine"]
        transaction = implementation._mutation_lookup(self.project, request_id)
        reserved_run_id = transaction["run_id"]
        self.assertEqual(transaction["phase"], "bound")
        self.assertFalse((implementation._runs_root(self.project) / reserved_run_id / "run.json").exists())

        ledger = implementation._mutation_database_path(self.project)
        ledger_preimage = ledger.read_bytes()
        storage_preimage = sorted(path.name for path in ledger.parent.iterdir())
        status, status_error = self.server().call_tool(
            "workflow_status",
            {"project_root": str(self.project), "request_id": request_id},
        )
        self.assertFalse(status_error, status)
        self.assertEqual(status["data"]["run_id"], reserved_run_id)
        self.assertEqual(status["data"]["start_state"], "bound")
        self.assertEqual(status["data"]["observed_status"], "unknown")
        self.assertFalse(status["data"]["terminal"])
        self.assertEqual(ledger.read_bytes(), ledger_preimage)
        self.assertEqual(sorted(path.name for path in ledger.parent.iterdir()), storage_preimage)

        recovered = self.direct_run(request_id)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["run_id"], reserved_run_id)

    def test_recorded_and_response_crashes_retry_one_reserved_run(self) -> None:
        implementation = sys.modules["workflow_runtime.engine"]
        for failpoint in ("after-run-recorded", "before-run-response"):
            with self.subTest(failpoint=failpoint):
                request_id = str(uuid.uuid4())
                crashed = self.direct_run(request_id, failpoint=failpoint)
                self.assertEqual(crashed.returncode, 86, crashed.stderr)
                reserved_run_id = implementation._mutation_lookup(self.project, request_id)["run_id"]
                recovered = self.direct_run(request_id)
                self.assertEqual(recovered.returncode, 0, recovered.stderr)
                self.assertEqual(json.loads(recovered.stdout)["run_id"], reserved_run_id)
                matching = [
                    path
                    for path in implementation._runs_root(self.project).glob(f"{reserved_run_id}*")
                    if path.is_dir()
                ]
                self.assertEqual([path.name for path in matching], [reserved_run_id])

    def test_worker_claim_crash_is_recoverable_without_duplicate_run(self) -> None:
        request_id = str(uuid.uuid4())
        first = self.direct_run(request_id, failpoint="after-worker-claim")
        self.assertEqual(first.returncode, 0, first.stderr)
        run_id = json.loads(first.stdout)["run_id"]
        implementation = sys.modules["workflow_runtime.engine"]
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            transaction = implementation._mutation_lookup(self.project, request_id)
            if transaction and not implementation._mutation_worker_is_live(transaction):
                break
            time.sleep(0.02)
        recovered = self.direct_run(request_id)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["run_id"], run_id)

    def test_legacy_split_registry_entries_remain_readable_and_ambiguity_fails_closed(self) -> None:
        server = self.server()
        implementation = sys.modules["workflow_runtime.engine"]
        for index, schema in enumerate(
            ("codex-workflow-mcp-run-request.v1", "codex-workflow-mcp-control-request.v1")
        ):
            request_id = str(uuid.uuid4())
            path = implementation._mcp_legacy_request_paths(self.project, request_id)[index + 1]
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            implementation._atomic_json(
                path,
                {
                    "schema_version": schema,
                    "request_id": request_id,
                    "request_digest": "0" * 64,
                    "phase": "bound",
                },
            )
            status, status_error = server.call_tool(
                "workflow_status",
                {"project_root": str(self.project), "request_id": request_id},
            )
            self.assertFalse(status_error, status)
            self.assertEqual(status["data"]["request_id"], request_id)
            self.assertEqual(status["data"]["start_state"], "bound")
            self.assertFalse(implementation._mutation_database_path(self.project).exists())

        ambiguous_id = str(uuid.uuid4())
        legacy_paths = implementation._mcp_legacy_request_paths(self.project, ambiguous_id)[1:]
        for path, schema in zip(
            legacy_paths,
            ("codex-workflow-mcp-run-request.v1", "codex-workflow-mcp-control-request.v1"),
            strict=True,
        ):
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            implementation._atomic_json(
                path,
                {
                    "schema_version": schema,
                    "request_id": ambiguous_id,
                    "request_digest": "0" * 64,
                    "phase": "bound",
                },
            )
        ambiguous, ambiguous_error = server.call_tool(
            "workflow_status",
            {"project_root": str(self.project), "request_id": ambiguous_id},
        )
        self.assertTrue(ambiguous_error)
        self.assertEqual(ambiguous["error"]["code"], "cli_error")
        self.assertIn("multiple registry entries", ambiguous["error"]["message"])

    def test_legacy_request_retry_is_read_only_and_byte_exact(self) -> None:
        implementation = sys.modules["workflow_runtime.engine"]
        request_id = str(uuid.uuid4())
        normalized = implementation._mcp_normalized_run_request(
            SimpleNamespace(
                workflow="builtin:fanout-synthesize",
                inputs=None,
                input=['request="summarize"', "items=[1]"],
                max_parallel=None,
                max_calls=5000,
                allow_workspace_write=False,
                allow_danger_full_access=False,
            ),
            self.project,
        )
        path = implementation._mcp_legacy_request_paths(self.project, request_id)[0]
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        implementation._atomic_json(
            path,
            {
                "schema_version": "codex-workflow-mcp-request.v1",
                "operation": "run",
                "request_id": request_id,
                "request_digest": implementation.digest_json(normalized),
                "phase": "bound",
                "created_at": "2026-08-21T00:00:00.000000+00:00",
            },
        )
        preimage = path.read_bytes()
        result, is_error = self.server().call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "request_id": request_id,
                "inputs": {"request": "summarize", "items": [1]},
            },
        )
        self.assertTrue(is_error)
        self.assertIn("read-only", result["error"]["message"])
        self.assertEqual(path.read_bytes(), preimage)
        self.assertIsNone(implementation._mutation_lookup(self.project, request_id))

    def test_mcp_status_does_not_rebuild_loop_projection(self) -> None:
        implementation = sys.modules["workflow_runtime.engine"]
        run_id = "exec_20260821T000000Z_12345678"
        run_dir = implementation._runs_root(self.project) / run_id
        run_dir.mkdir(parents=True, mode=0o700)
        checkpoint = {"cycle_id": 0}
        state = {
            "schema_version": "codex-exec-loop-run.v1",
            "run_id": run_id,
            "loop_id": "loop-test",
            "workflow_id": "loop-monitor",
            "workflow_scope": "builtin",
            "workflow_digest": "0" * 64,
            "project_root": str(self.project),
            "status": "paused",
            "created_at": "2026-08-21T00:00:00+00:00",
            "updated_at": "2026-08-21T00:00:00+00:00",
            "checkpoint": checkpoint,
            "current_cycle_id": None,
            "last_cycle_id": 0,
            "last_completed_cycle_id": 0,
            "last_task_summary": {},
            "call_usage": {"planned": 1, "attempted": 0},
            "consecutive_failures": 0,
            "circuit_breaker": "closed",
            "next_wake_at": None,
            "tasks": {},
        }
        implementation._atomic_json(run_dir / "run.json", state)
        event = implementation._loop_event_record(state, "loop.paused", {}, 1, None)
        (run_dir / "state.jsonl").write_text(json.dumps(event, sort_keys=True) + "\n", encoding="utf-8")
        projection = run_dir / "STATE.md"
        self.assertFalse(projection.exists())
        completed = subprocess.run(
            [
                sys.executable,
                str(PLUGIN_ROOT / "scripts" / "codex_workflows.py"),
                "--project-root",
                str(self.project),
                "status",
                run_id,
                "--mcp-json",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=os.environ,
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["observed_status"], "paused")
        self.assertFalse(projection.exists())

    def test_unknown_request_status_does_not_create_run_storage(self) -> None:
        implementation = sys.modules["workflow_runtime.engine"]
        runs_root = implementation._runs_root(self.project)
        self.assertFalse(runs_root.exists())
        status, status_error = self.server().call_tool(
            "workflow_status",
            {"project_root": str(self.project), "request_id": str(uuid.uuid4())},
        )
        self.assertTrue(status_error)
        self.assertIn("unknown MCP mutation request", status["error"]["message"])
        self.assertFalse(runs_root.exists())

    def test_control_matrix_rejects_pause_for_finite_run(self) -> None:
        server = self.server()
        request_id = str(uuid.uuid4())
        run, run_error = server.call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "request_id": request_id,
                "inputs": {"request": "summarize", "items": [1]},
            },
        )
        self.assertFalse(run_error, run)
        control, control_error = server.call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run["data"]["run_id"],
                "request_id": str(uuid.uuid4()),
                "action": "pause",
            },
        )
        self.assertTrue(control_error)
        self.assertEqual(control["error"]["code"], "cli_error")
        self.assertIn("unsupported", control["error"]["message"])

    def test_persistent_pause_resume_retry_and_cancel_converge(self) -> None:
        server = self.server()
        run_request = str(uuid.uuid4())
        run, run_error = server.call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:loop-monitor",
                "request_id": run_request,
                "inputs": {"cursor": "initial", "instance": "mcp-test", "request": "monitor"},
            },
        )
        self.assertFalse(run_error, run)
        run_id = run["data"]["run_id"]

        def wait_for(expected: set[str]) -> dict[str, object]:
            deadline = time.monotonic() + 15
            latest = None
            while time.monotonic() < deadline:
                latest, status_error = server.call_tool(
                    "workflow_status",
                    {"project_root": str(self.project), "run_id": run_id},
                )
                self.assertFalse(status_error, latest)
                if latest["data"]["observed_status"] in expected:
                    return latest
                time.sleep(0.05)
            self.fail(f"run did not reach {expected}: {latest}")

        wait_for({"sleeping"})
        pause_id = str(uuid.uuid4())
        crashed_pause = self.direct_control(
            run_id,
            pause_id,
            "pause",
            failpoint="before-control-response",
        )
        self.assertEqual(crashed_pause.returncode, 86, crashed_pause.stderr)
        paused, pause_error = server.call_tool(
            "workflow_control",
            {"project_root": str(self.project), "run_id": run_id, "request_id": pause_id, "action": "pause"},
        )
        self.assertFalse(pause_error, paused)
        self.assertTrue(paused["data"]["accepted"])
        wait_for({"paused"})

        resume_id = str(uuid.uuid4())
        resume_arguments = {
            "project_root": str(self.project),
            "run_id": run_id,
            "request_id": resume_id,
            "action": "resume",
        }
        resumed, resume_error = server.call_tool("workflow_control", resume_arguments)
        self.assertFalse(resume_error, resumed)
        repeated, repeated_error = server.call_tool("workflow_control", resume_arguments)
        self.assertFalse(repeated_error, repeated)
        self.assertEqual(repeated["data"]["run_id"], run_id)
        wait_for({"running", "sleeping"})

        cancelled, cancel_error = server.call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run_id,
                "request_id": str(uuid.uuid4()),
                "action": "cancel",
            },
        )
        self.assertFalse(cancel_error, cancelled)
        wait_for({"cancelled"})

    def test_interrupted_resume_reconciles_already_applied_loop_state(self) -> None:
        server = self.server()
        run, run_error = server.call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:loop-monitor",
                "request_id": str(uuid.uuid4()),
                "inputs": {"cursor": "initial", "instance": "resume-crash", "request": "monitor"},
            },
        )
        self.assertFalse(run_error, run)
        run_id = run["data"]["run_id"]

        def wait_for(expected: set[str]) -> None:
            deadline = time.monotonic() + 15
            latest = None
            while time.monotonic() < deadline:
                latest, status_error = server.call_tool(
                    "workflow_status",
                    {"project_root": str(self.project), "run_id": run_id},
                )
                self.assertFalse(status_error, latest)
                if latest["data"]["observed_status"] in expected:
                    return
                time.sleep(0.05)
            self.fail(f"run did not reach {expected}: {latest}")

        wait_for({"sleeping"})
        paused, pause_error = server.call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run_id,
                "request_id": str(uuid.uuid4()),
                "action": "pause",
            },
        )
        self.assertFalse(pause_error, paused)
        wait_for({"paused"})

        resume_id = str(uuid.uuid4())
        crashed = self.direct_control(
            run_id,
            resume_id,
            "resume",
            failpoint="after-control-applied",
        )
        self.assertEqual(crashed.returncode, 86, crashed.stderr)
        implementation = sys.modules["workflow_runtime.engine"]
        self.assertEqual(implementation._mutation_lookup(self.project, resume_id)["phase"], "bound")

        resumed, resume_error = server.call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run_id,
                "request_id": resume_id,
                "action": "resume",
            },
        )
        self.assertFalse(resume_error, resumed)
        wait_for({"running", "sleeping"})
        cancelled, cancel_error = server.call_tool(
            "workflow_control",
            {
                "project_root": str(self.project),
                "run_id": run_id,
                "request_id": str(uuid.uuid4()),
                "action": "cancel",
            },
        )
        self.assertFalse(cancel_error, cancelled)
        wait_for({"cancelled"})

    def test_registry_permissions_drift_and_revoke_are_enforced(self) -> None:
        registry_dir = self.codex_home / "workflow-governor-mcp"
        registry = registry_dir / "authorized-roots.json"
        self.assertEqual(stat.S_IMODE(registry_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(registry.stat().st_mode), 0o600)
        registry.chmod(0o644)
        with self.assertRaises(root_auth.RootAuthorizationError):
            root_auth.open_authorized(self.project)
        registry.chmod(0o600)
        self.assertTrue(root_auth.revoke(self.project))
        with self.assertRaises(root_auth.RootAuthorizationError):
            root_auth.open_authorized(self.project)

    def test_mutation_ledger_rejects_permission_link_and_symlink_tamper(self) -> None:
        server = self.server()
        request_id = str(uuid.uuid4())
        run, run_error = server.call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "request_id": request_id,
                "inputs": {"request": "summarize", "items": [1]},
            },
        )
        self.assertFalse(run_error, run)
        implementation = sys.modules["workflow_runtime.engine"]
        ledger = implementation._mutation_database_path(self.project)

        ledger.chmod(0o644)
        invalid_mode, invalid_mode_error = server.call_tool(
            "workflow_status",
            {"project_root": str(self.project), "request_id": request_id},
        )
        self.assertTrue(invalid_mode_error)
        self.assertIn("mode 0600", invalid_mode["error"]["message"])
        ledger.chmod(0o600)

        alias = self.base / "ledger-hardlink"
        os.link(ledger, alias)
        hardlinked, hardlinked_error = server.call_tool(
            "workflow_status",
            {"project_root": str(self.project), "request_id": request_id},
        )
        self.assertTrue(hardlinked_error)
        self.assertIn("one link", hardlinked["error"]["message"])

    def test_mutation_ledger_symlink_fails_before_request_persistence(self) -> None:
        implementation = sys.modules["workflow_runtime.engine"]
        ledger = implementation._mutation_database_path(self.project)
        ledger.parent.mkdir(parents=True, mode=0o700)
        target = self.base / "ledger-target"
        target.write_bytes(b"")
        target.chmod(0o600)
        ledger.symlink_to(target)
        result, is_error = self.server().call_tool(
            "workflow_run",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "request_id": str(uuid.uuid4()),
                "inputs": {"request": "summarize", "items": [1]},
            },
        )
        self.assertTrue(is_error)
        self.assertIn("regular file", result["error"]["message"])
        self.assertEqual(target.read_bytes(), b"")

    def test_authorization_rejects_symlink_alias_and_credential_origin(self) -> None:
        alias = self.base / "project-alias"
        alias.symlink_to(self.project, target_is_directory=True)
        with self.assertRaises(root_auth.RootAuthorizationError):
            root_auth.authorize(alias)
        credential = self.base / "credential-project"
        credential.mkdir()
        _git(credential)
        subprocess.run(
            ["git", "-C", str(credential), "remote", "add", "origin", "https://user:password@example.invalid/repo.git"],
            check=True,
        )
        with self.assertRaises(root_auth.RootAuthorizationError):
            root_auth.authorize(credential)

    def test_authorization_handles_linked_worktree_concurrency_and_origin_drift(self) -> None:
        linked = self.base / "linked-worktree"
        subprocess.run(
            ["git", "-C", str(self.project), "worktree", "add", "-qb", "linked-test", str(linked)],
            check=True,
        )
        with ThreadPoolExecutor(max_workers=4) as executor:
            records = list(executor.map(lambda _: root_auth.authorize(linked), range(4)))
        self.assertTrue(all(record["project_root"] == str(linked) for record in records))
        with root_auth.open_authorized(linked) as authorized:
            authorized.verify()

        subprocess.run(
            ["git", "-C", str(self.project), "remote", "add", "origin", "https://example.invalid/repo.git"],
            check=True,
        )
        with self.assertRaises(root_auth.RootAuthorizationError):
            root_auth.open_authorized(self.project)

    def test_protocol_notifications_and_unknown_fields_are_bounded(self) -> None:
        server = self.server()
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}))
        self.assertIsNone(server.handle({"jsonrpc": "2.0", "method": "ping"}))
        id_bearing = server.handle(
            {"jsonrpc": "2.0", "id": 7, "method": "notifications/initialized", "params": {}}
        )
        self.assertEqual(id_bearing["id"], 7)
        self.assertEqual(id_bearing["error"]["code"], -32601)
        result, is_error = server.call_tool(
            "workflow_plan",
            {
                "project_root": str(self.project),
                "workflow": "builtin:fanout-synthesize",
                "unexpected": True,
            },
        )
        self.assertTrue(is_error)
        self.assertEqual(result["error"]["code"], "invalid_arguments")

    def test_protocol_negotiation_redaction_and_complete_response_bound(self) -> None:
        server = self.server()
        unsupported = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "bogus-version"},
            }
        )
        self.assertEqual(unsupported["result"]["protocolVersion"], DEFAULT_PROTOCOL_VERSION)
        supported = server.handle(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        self.assertEqual(supported["result"]["protocolVersion"], "2024-11-05")

        secret = "Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.signature\npassword=very secret phrase"
        sanitized = _sanitize(secret)
        implementation = sys.modules["workflow_runtime.engine"]
        cli_sanitized = implementation._redact_text(secret)
        for value in (sanitized, cli_sanitized):
            self.assertNotIn("eyJhbGci", value)
            self.assertNotIn("very secret phrase", value)

        standalone = " ".join(
            (
                "xox" + "b-1234567890-abcdefghijklmnop",
                "gl" + "pat-abcdefghijklmnopqrstuv",
                "AK" + "IAABCDEFGHIJKLMNOP",
                "github_" + "pat_ABCDEFGHIJKLMNOPQRSTUVWXYZ123456",
            )
        )
        for value in (_sanitize(standalone), implementation._redact_text(standalone)):
            self.assertNotIn("xoxb-", value)
            self.assertNotIn("glpat-", value)
            self.assertNotIn("AKIA", value)
            self.assertNotIn("github_pat_", value)
        self.assertEqual(
            redaction.redact_value(
                {
                    "access_token": "unstructured-value",
                    "AWS_SECRET_ACCESS_KEY": "aws-secret-value",
                    "safe": "visible",
                }
            ),
            {
                "access_token": "[REDACTED]",
                "AWS_SECRET_ACCESS_KEY": "[REDACTED]",
                "safe": "visible",
            },
        )
        self.assertEqual(
            implementation._redact_value({"AWS_SECRET_ACCESS_KEY": "aws-secret-value"}),
            {"AWS_SECRET_ACCESS_KEY": "[REDACTED]"},
        )
        aws_assignments = (
            "AWS_SECRET_ACCESS_KEY=super-secret-value",
            "aws_secret_access_key: super-secret-value",
            "AWS_SECRET_ACCESS_KEY='super secret value'",
            '"AWS_SECRET_ACCESS_KEY":"super-secret-value"',
        )
        for assignment in aws_assignments:
            for value in (
                redaction.redact_text(assignment),
                _sanitize(assignment),
                implementation._redact_text(assignment),
            ):
                self.assertNotIn("super-secret-value", value)
                self.assertNotIn("super secret value", value)

        with patch(
            "mcp.server._run_bounded",
            return_value=(1, b"", f"failure {standalone}".encode("utf-8")),
        ):
            failure, failure_error = server.call_tool(
                "workflow_plan",
                {
                    "project_root": str(self.project),
                    "workflow": "builtin:fanout-synthesize",
                    "inputs": {"request": "summarize", "items": [1]},
                },
            )
        self.assertTrue(failure_error)
        failure_text = json.dumps(failure, sort_keys=True)
        self.assertNotIn("xoxb-", failure_text)
        self.assertNotIn("glpat-", failure_text)
        self.assertNotIn("AKIA", failure_text)
        self.assertNotIn("github_pat_", failure_text)

        oversized = {
            "schema_version": RESULT_SCHEMA,
            "ok": True,
            "tool": "workflow_plan",
            "data": {"payload": "x" * 600_000},
        }
        with patch.object(server, "call_tool", return_value=(oversized, False)):
            response = server.handle(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "workflow_plan", "arguments": {}},
                }
            )
        encoded = json.dumps(response, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.assertLessEqual(len(encoded), MAX_RESPONSE_BYTES)
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(response["result"]["structuredContent"]["error"]["code"], "response_limit")

        malformed_id = server.handle(
            {"jsonrpc": "2.0", "id": ["x" * 500_000], "method": "tools/list", "params": {}}
        )
        self.assertIsNone(malformed_id["id"])
        self.assertEqual(malformed_id["error"]["code"], -32600)
        self.assertLessEqual(
            len(json.dumps(malformed_id, ensure_ascii=False, separators=(",", ":")).encode("utf-8")),
            MAX_RESPONSE_BYTES,
        )
        with patch.object(server, "tool_list", return_value=[{"description": "x" * (MAX_RESPONSE_BYTES + 1)}]):
            bounded_list = server.handle(
                {"jsonrpc": "2.0", "id": 4, "method": "tools/list", "params": {}}
            )
        self.assertIsNone(bounded_list["id"])
        self.assertEqual(bounded_list["error"]["code"], -32603)

        too_large_numeric = server.handle(
            {"jsonrpc": "2.0", "id": 10**1000, "method": "ping"}
        )
        self.assertIsNone(too_large_numeric["id"])
        self.assertEqual(too_large_numeric["error"]["code"], -32600)

        for numeric_id in ("9" * 5000, "-" + "9" * 5000):
            completed = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "mcp" / "server.py")],
                input=f'{{"jsonrpc":"2.0","id":{numeric_id},"method":"ping"}}\n',
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=os.environ,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            oversized_integer = json.loads(completed.stdout)
            self.assertIsNone(oversized_integer["id"])
            self.assertIn(oversized_integer["error"]["code"], {-32700, -32600})
            self.assertLessEqual(len(completed.stdout.encode("utf-8")), MAX_RESPONSE_BYTES + 1)

    def test_direct_cli_process_does_not_import_legacy_package(self) -> None:
        sentinel = self.base / "sentinel"
        package = sentinel / "workflow_runtime"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("raise RuntimeError('external runtime imported')\n", encoding="utf-8")
        completed = subprocess.run(
            [
                sys.executable,
                str(PLUGIN_ROOT / "scripts" / "codex_workflows.py"),
                "--project-root",
                str(self.project),
                "--mcp-qualified-only",
                "plan",
                "builtin:fanout-synthesize",
                "--input",
                'request="summarize"',
                "--input",
                "items=[1]",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={**os.environ, "PYTHONPATH": str(sentinel)},
            text=True,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_full_stdio_process_exercises_all_tools_without_legacy_runtime(self) -> None:
        sentinel = self.base / "stdio-sentinel"
        package = sentinel / "workflow_runtime"
        package.mkdir(parents=True)
        (package / "__init__.py").write_text("raise RuntimeError('external runtime imported')\n", encoding="utf-8")
        binary_dir = self.base / "bin"
        binary_dir.mkdir()
        _fake_codex(binary_dir / "codex")
        environment = {
            **os.environ,
            "PYTHONPATH": str(sentinel),
            "PATH": f"{binary_dir}{os.pathsep}{os.environ.get('PATH', '')}",
        }

        def call(request: dict[str, object]) -> dict[str, object]:
            completed = subprocess.run(
                [sys.executable, str(PLUGIN_ROOT / "mcp" / "server.py")],
                input=json.dumps(request, ensure_ascii=False) + "\n",
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=environment,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            return json.loads(completed.stdout)

        plan = call(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "workflow_plan",
                    "arguments": {
                        "project_root": str(self.project),
                        "workflow": "builtin:fanout-synthesize",
                        "inputs": {"request": "summarize", "items": [1]},
                    },
                },
            }
        )
        self.assertFalse(plan["result"]["isError"], plan)

        request_id = str(uuid.uuid4())
        started = call(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "workflow_run",
                    "arguments": {
                        "project_root": str(self.project),
                        "workflow": "builtin:fanout-synthesize",
                        "request_id": request_id,
                        "inputs": {"request": "summarize", "items": [1]},
                    },
                },
            }
        )
        self.assertFalse(started["result"]["isError"], started)
        run_id = started["result"]["structuredContent"]["data"]["run_id"]

        status = call(
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "workflow_status",
                    "arguments": {"project_root": str(self.project), "request_id": request_id},
                },
            }
        )
        self.assertFalse(status["result"]["isError"], status)
        self.assertEqual(status["result"]["structuredContent"]["data"]["run_id"], run_id)

        controlled = call(
            {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "workflow_control",
                    "arguments": {
                        "project_root": str(self.project),
                        "run_id": run_id,
                        "request_id": str(uuid.uuid4()),
                        "action": "cancel",
                    },
                },
            }
        )
        self.assertFalse(controlled["result"]["isError"], controlled)


if __name__ == "__main__":
    unittest.main()
