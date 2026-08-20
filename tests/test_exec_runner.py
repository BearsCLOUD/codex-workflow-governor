from __future__ import annotations

import asyncio
import io
import json
import os
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workflow_governor.contracts import ContractError
from workflow_governor.exec_runner import EXEC_WORKFLOW_SCHEMA, execute_run, load_workflow, main


FAKE_CODEX = r'''#!/usr/bin/env python3
from __future__ import annotations

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path


def option(name: str) -> str:
    index = sys.argv.index(name)
    return sys.argv[index + 1]


def classify(prompt: str) -> dict[str, object]:
    match = re.search(r"FANOUT index=(\d+) item=(-?\d+)", prompt)
    if match:
        return {"kind": "fanout", "index": int(match.group(1))}
    if prompt.startswith("SYNTH "):
        return {"kind": "synthesis"}
    if prompt.startswith("FAIL"):
        return {"kind": "failure"}
    if prompt.startswith("MALFORMED"):
        return {"kind": "malformed"}
    for kind in (
        "terminal-missing",
        "terminal-malformed",
        "terminal-schema-invalid",
        "terminal-valid-grace",
        "terminal-descendant",
        "healthy-events",
    ):
        if prompt.startswith(kind.upper()):
            return {"kind": kind}
    return {"kind": "generic"}


state_path = Path(os.environ["FAKE_CODEX_STATE"])
state_path.parent.mkdir(parents=True, exist_ok=True)
prompt = sys.stdin.read()
identity = classify(prompt)


def update(event: str) -> None:
    with state_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        raw = handle.read()
        state = json.loads(raw) if raw else {
            "active": 0,
            "peak": 0,
            "starts": [],
            "finishes": [],
            "calls": [],
        }
        if event == "start":
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
            state["starts"].append(identity)
            state["calls"].append({
                "identity": identity,
                "argv": sys.argv[1:],
                "prompt": prompt,
            })
        else:
            state["active"] -= 1
            state["finishes"].append(identity)
        handle.seek(0)
        handle.truncate()
        json.dump(state, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def record_child(pid: int) -> None:
    with state_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        handle.seek(0)
        state = json.loads(handle.read())
        state.setdefault("child_pids", []).append(pid)
        handle.seek(0)
        handle.truncate()
        json.dump(state, handle, sort_keys=True)
        handle.flush()
        os.fsync(handle.fileno())
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


update("start")
try:
    if identity["kind"] == "fanout":
        index = int(identity["index"])
        # Initial workers finish in reverse order, making completion observably
        # different from input order while all remain live long enough to prove
        # that the configured concurrency bound is exercised.
        time.sleep((6 - index) * 0.04)
        match = re.search(r"FANOUT index=(\d+) item=(-?\d+)", prompt)
        assert match is not None
        output = {"index": index, "value": f"value-{int(match.group(2))}"}
    elif identity["kind"] == "synthesis":
        encoded = prompt[len("SYNTH "):].split(
            "\n\nReturn only the final JSON object", 1
        )[0]
        upstream = json.loads(encoded)
        output = {
            "indices": [item["index"] for item in upstream],
            "values": [item["value"] for item in upstream],
        }
    elif identity["kind"] == "failure":
        raise SystemExit(9)
    elif identity["kind"] == "malformed":
        final_path = Path(option("--output-last-message"))
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_text("{this is not json", encoding="utf-8")
        raise SystemExit(0)
    elif identity["kind"] == "healthy-events":
        for index in range(6):
            print(json.dumps({"type": "item.updated", "index": index}), flush=True)
            time.sleep(0.05)
        output = {"ok": True}
    elif str(identity["kind"]).startswith("terminal-"):
        final_path = Path(option("--output-last-message"))
        final_path.parent.mkdir(parents=True, exist_ok=True)
        print(json.dumps({"type": "turn.completed"}), flush=True)
        if identity["kind"] == "terminal-malformed":
            final_path.write_text("{invalid", encoding="utf-8")
        elif identity["kind"] == "terminal-schema-invalid":
            final_path.write_text(json.dumps({"ok": "not-a-boolean"}), encoding="utf-8")
        elif identity["kind"] == "terminal-valid-grace":
            time.sleep(0.1)
            final_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        elif identity["kind"] == "terminal-descendant":
            child = subprocess.Popen(["sleep", "60"])
            record_child(child.pid)
        time.sleep(60)
        raise SystemExit(0)
    else:
        output = {"ok": True}

    final_path = Path(option("--output-last-message"))
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(json.dumps(output), encoding="utf-8")
    print(json.dumps({"type": "fake.completed", "identity": identity}))
finally:
    update("finish")
'''


OBJECT_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}

FANOUT_SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "value": {"type": "string"},
    },
    "required": ["index", "value"],
    "additionalProperties": False,
}

SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "indices": {"type": "array", "items": {"type": "integer"}},
        "values": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["indices", "values"],
    "additionalProperties": False,
}


def process_is_live(pid: int) -> bool:
    try:
        tail = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").rsplit(")", 1)[1]
    except OSError:
        return False
    return tail.split()[0] != "Z"


class ExecRunnerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / ".git").mkdir()
        self.data = self.root / "plugin-data"
        self.fake_state = self.root / "fake-state.json"
        self.fake_codex = self.root / "fake-codex"
        self.fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
        self.fake_codex.chmod(
            self.fake_codex.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )
        self.environment = patch.dict(
            os.environ,
            {
                "PLUGIN_DATA": str(self.data),
                "FAKE_CODEX_STATE": str(self.fake_state),
            },
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def write_workflow(
        self,
        name: str,
        tasks: list[dict[str, object]],
        schemas: dict[str, dict[str, object]],
        *,
        inputs: dict[str, str] | None = None,
        max_parallel: int = 3,
    ) -> Path:
        directory = self.project / ".codex" / "exec-workflows" / name
        (directory / "schemas").mkdir(parents=True)
        for schema_name, schema in schemas.items():
            (directory / "schemas" / f"{schema_name}.json").write_text(
                json.dumps(schema), encoding="utf-8"
            )
        workflow = {
            "schema_version": EXEC_WORKFLOW_SCHEMA,
            "workflow_id": name,
            "description": f"Test workflow {name}",
            "max_parallel": max_parallel,
            "inputs": inputs or {},
            "tasks": tasks,
        }
        (directory / "workflow.json").write_text(
            json.dumps(workflow), encoding="utf-8"
        )
        return directory

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--project-root", str(self.project), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def only_run_dir(self) -> Path:
        run_files = list(self.data.glob("exec-runs/*/*/run.json"))
        self.assertEqual(len(run_files), 1, run_files)
        return run_files[0].parent

    def fake_log(self) -> dict[str, object]:
        return json.loads(self.fake_state.read_text(encoding="utf-8"))


class ExecutionTests(ExecRunnerTestCase):
    def test_fanout_is_bounded_ordered_formal_and_persisted(self) -> None:
        workflow = self.write_workflow(
            "fanout-synthesis",
            [
                {
                    "id": "inspect",
                    "depends_on": [],
                    "foreach": "inputs.items",
                    "item_name": "item",
                    "prompt": "FANOUT index={{ index }} item={{ item }}",
                    "output_schema": "schemas/inspect.json",
                },
                {
                    "id": "synthesize",
                    "depends_on": ["inspect"],
                    "prompt": "SYNTH {{ tasks.inspect.output }}",
                    "output_schema": "schemas/synthesize.json",
                },
            ],
            {"inspect": FANOUT_SCHEMA, "synthesize": SYNTHESIS_SCHEMA},
            inputs={"items": "array"},
            max_parallel=3,
        )

        code, stdout, stderr = self.invoke(
            "run",
            str(workflow),
            "--input",
            "items=[0,1,2,3,4]",
            "--codex-bin",
            str(self.fake_codex),
        )

        self.assertEqual(code, 0, stderr)
        self.assertIn("completed", stdout)
        run_dir = self.only_run_dir()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        expected = {
            "indices": [0, 1, 2, 3, 4],
            "values": ["value-0", "value-1", "value-2", "value-3", "value-4"],
        }
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["tasks"]["inspect"]["status"], "completed")
        self.assertEqual(state["tasks"]["inspect"]["total"], 5)
        self.assertEqual(state["tasks"]["inspect"]["completed_items"], 5)
        self.assertEqual(state["tasks"]["synthesize"]["status"], "completed")
        self.assertEqual(
            json.loads((run_dir / "tasks" / "synthesize" / "final.json").read_text()),
            expected,
        )

        log = self.fake_log()
        self.assertEqual(log["active"], 0)
        self.assertEqual(log["peak"], 3)
        finish_order = [
            event["index"]
            for event in log["finishes"]
            if event["kind"] == "fanout"
        ]
        self.assertNotEqual(finish_order, [0, 1, 2, 3, 4])
        synthesis_call = next(
            call for call in log["calls"] if call["identity"]["kind"] == "synthesis"
        )
        rendered_upstream = json.loads(
            synthesis_call["prompt"][len("SYNTH "):].split(
                "\n\nReturn only the final JSON object", 1
            )[0]
        )
        self.assertEqual([item["index"] for item in rendered_upstream], [0, 1, 2, 3, 4])
        for call in log["calls"]:
            self.assertIn("--json", call["argv"])
            self.assertIn("--output-schema", call["argv"])
            self.assertIn("--output-last-message", call["argv"])

        result_code, result_stdout, result_stderr = self.invoke(
            "result", state["run_id"]
        )
        self.assertEqual(result_code, 0, result_stderr)
        self.assertEqual(json.loads(result_stdout), {"synthesize": expected})

    def test_failed_task_blocks_dependent_task(self) -> None:
        workflow = self.write_workflow(
            "blocking",
            [
                {
                    "id": "explode",
                    "depends_on": [],
                    "prompt": "FAIL primary",
                    "output_schema": "schemas/out.json",
                },
                {
                    "id": "after",
                    "depends_on": ["explode"],
                    "prompt": "NEVER {{ tasks.explode.output }}",
                    "output_schema": "schemas/out.json",
                },
            ],
            {"out": OBJECT_SCHEMA},
        )

        code, _, _ = self.invoke(
            "run", str(workflow), "--codex-bin", str(self.fake_codex)
        )

        self.assertEqual(code, 1)
        run_dir = self.only_run_dir()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["tasks"]["explode"]["status"], "failed")
        self.assertEqual(state["tasks"]["after"]["status"], "blocked")
        self.assertEqual(state["tasks"]["after"]["error"], "dependency did not complete")
        self.assertFalse((run_dir / "tasks" / "after").exists())
        self.assertEqual(
            [event["kind"] for event in self.fake_log()["starts"]], ["failure"]
        )

    def test_malformed_final_output_fails_closed(self) -> None:
        workflow = self.write_workflow(
            "malformed",
            [
                {
                    "id": "invalid",
                    "depends_on": [],
                    "prompt": "MALFORMED response",
                    "output_schema": "schemas/out.json",
                }
            ],
            {"out": OBJECT_SCHEMA},
        )

        code, _, _ = self.invoke(
            "run", str(workflow), "--codex-bin", str(self.fake_codex)
        )

        self.assertEqual(code, 1)
        run_dir = self.only_run_dir()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["tasks"]["invalid"]["status"], "failed")
        self.assertIn("Expecting property name", state["tasks"]["invalid"]["error"])
        self.assertFalse((run_dir / "tasks" / "invalid" / "final.json").exists())
        attempt = json.loads(
            (run_dir / "tasks" / "invalid" / "attempt-1" / "attempt.json").read_text()
        )
        self.assertEqual(attempt["status"], "failed")

    def test_detached_worker_preflight_failure_marks_run_terminal(self) -> None:
        workflow = self.write_workflow(
            "detached-preflight",
            [
                {
                    "id": "work",
                    "depends_on": [],
                    "prompt": "WORK",
                    "output_schema": "schemas/out.json",
                }
            ],
            {"out": OBJECT_SCHEMA},
        )

        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                str(workflow),
                "--codex-bin",
                str(self.fake_codex),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        snapshot = run_dir / "workflow" / "workflow.json"
        changed = json.loads(snapshot.read_text(encoding="utf-8"))
        changed["description"] = "tampered after queueing"
        snapshot.write_text(json.dumps(changed), encoding="utf-8")

        worker_code, _, worker_stderr = self.invoke("_worker", str(run_dir))

        self.assertEqual(worker_code, 2)
        self.assertIn("snapshot no longer matches", worker_stderr)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        self.assertIn("snapshot no longer matches", state["error"])
        self.assertIn("finished_at", state)

    def test_write_sandbox_is_disclosed_and_requires_explicit_opt_in(self) -> None:
        workflow = self.write_workflow(
            "write-sandbox",
            [
                {
                    "id": "work",
                    "depends_on": [],
                    "prompt": "WORK",
                    "output_schema": "schemas/out.json",
                    "sandbox": "workspace-write",
                    "cwd": ".",
                }
            ],
            {"out": OBJECT_SCHEMA},
        )

        plan_code, plan_stdout, plan_stderr = self.invoke("plan", str(workflow))
        self.assertEqual(plan_code, 0, plan_stderr)
        plan = json.loads(plan_stdout)
        self.assertEqual(plan["tasks"][0]["sandbox"], "workspace-write")
        self.assertEqual(plan["tasks"][0]["cwd"], ".")

        rejected_code, _, rejected_stderr = self.invoke(
            "run", str(workflow), "--codex-bin", str(self.fake_codex)
        )
        self.assertEqual(rejected_code, 2)
        self.assertIn("--allow-workspace-write", rejected_stderr)
        self.assertEqual(list(self.data.glob("exec-runs/*/*/run.json")), [])

        accepted_code, _, accepted_stderr = self.invoke(
            "run",
            str(workflow),
            "--codex-bin",
            str(self.fake_codex),
            "--allow-workspace-write",
        )
        self.assertEqual(accepted_code, 0, accepted_stderr)

    def test_external_worker_cancellation_marks_run_and_tasks_terminal(self) -> None:
        workflow = self.write_workflow(
            "external-cancellation",
            [
                {
                    "id": "work",
                    "depends_on": [],
                    "prompt": "FANOUT index=0 item=0",
                    "output_schema": "schemas/out.json",
                }
            ],
            {"out": FANOUT_SCHEMA},
        )
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                str(workflow),
                "--codex-bin",
                str(self.fake_codex),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()

        async def cancel_active_run() -> None:
            worker = asyncio.create_task(execute_run(run_dir))
            await asyncio.sleep(0.05)
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

        asyncio.run(cancel_active_run())

        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "cancelled")
        self.assertEqual(state["tasks"]["work"]["status"], "cancelled")
        self.assertIn("finished_at", state)

    def test_terminal_event_without_output_retries_once_and_kills_process_group(self) -> None:
        workflow = self.write_workflow(
            "terminal-retry",
            [
                {
                    "id": "work",
                    "depends_on": [],
                    "prompt": "TERMINAL-DESCENDANT",
                    "output_schema": "schemas/out.json",
                    "retries": 1,
                },
                {
                    "id": "after",
                    "depends_on": ["work"],
                    "prompt": "WORK",
                    "output_schema": "schemas/out.json",
                },
            ],
            {"out": OBJECT_SCHEMA},
        )
        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.1"}):
            code, _, stderr = self.invoke(
                "run", str(workflow), "--codex-bin", str(self.fake_codex)
            )

        self.assertEqual(code, 1, stderr)
        run_dir = self.only_run_dir()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        task = state["tasks"]["work"]
        self.assertEqual(state["status"], "failed")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["attempt"], 2)
        self.assertEqual(task["failure_reason"], "terminal_event_output_missing")
        self.assertEqual(task["reconciliation_reason"], "terminal_event_without_valid_output")
        self.assertEqual(task["output_validation_state"], "missing")
        self.assertEqual(task["next_action"], "fail_task")
        self.assertIsNotNone(task["last_activity_at"])
        self.assertIsNotNone(task["last_worker_heartbeat"])
        self.assertIsNotNone(task["last_event_at"])
        self.assertIsNotNone(task["terminal_event_at"])
        self.assertIsNotNone(task["process_exit_at"])
        self.assertEqual(state["tasks"]["after"]["status"], "blocked")
        starts = self.fake_log()["starts"]
        self.assertEqual([item["kind"] for item in starts], ["terminal-descendant"] * 2)
        events = [
            json.loads(line)
            for line in (run_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        reconciliations = [item for item in events if item["type"] == "attempt.reconciled"]
        self.assertEqual(len(reconciliations), 2)
        child_pids = self.fake_log().get("child_pids", [])
        self.assertEqual(len(child_pids), 2)
        for _ in range(20):
            if not any(process_is_live(pid) for pid in child_pids):
                break
            time.sleep(0.05)
        self.assertFalse(any(process_is_live(pid) for pid in child_pids))

    def test_terminal_output_failures_have_distinct_stable_metadata(self) -> None:
        modes = {
            "missing": ("TERMINAL-MISSING", "terminal_event_output_missing", "missing"),
            "malformed": (
                "TERMINAL-MALFORMED",
                "terminal_event_output_malformed",
                "malformed",
            ),
            "schema": (
                "TERMINAL-SCHEMA-INVALID",
                "terminal_event_output_schema_invalid",
                "schema-invalid",
            ),
        }
        workflow = self.write_workflow(
            "terminal-output-states",
            [
                {
                    "id": task_id,
                    "depends_on": [],
                    "prompt": prompt,
                    "output_schema": "schemas/out.json",
                }
                for task_id, (prompt, _, _) in modes.items()
            ],
            {"out": OBJECT_SCHEMA},
        )
        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.1"}):
            code, _, stderr = self.invoke(
                "run", str(workflow), "--codex-bin", str(self.fake_codex)
            )

        self.assertEqual(code, 1, stderr)
        state = json.loads((self.only_run_dir() / "run.json").read_text(encoding="utf-8"))
        for task_id, (_, failure_reason, output_state) in modes.items():
            with self.subTest(task=task_id):
                task = state["tasks"][task_id]
                self.assertEqual(task["failure_reason"], failure_reason)
                self.assertEqual(task["output_validation_state"], output_state)

    def test_valid_output_during_grace_and_healthy_events_complete(self) -> None:
        workflow = self.write_workflow(
            "terminal-success",
            [
                {
                    "id": "grace",
                    "depends_on": [],
                    "prompt": "TERMINAL-VALID-GRACE",
                    "output_schema": "schemas/out.json",
                },
                {
                    "id": "healthy",
                    "depends_on": [],
                    "prompt": "HEALTHY-EVENTS",
                    "output_schema": "schemas/out.json",
                },
            ],
            {"out": OBJECT_SCHEMA},
        )
        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.4"}):
            code, _, stderr = self.invoke(
                "run", str(workflow), "--codex-bin", str(self.fake_codex)
            )

        self.assertEqual(code, 0, stderr)
        state = json.loads((self.only_run_dir() / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(
            state["tasks"]["grace"]["reconciliation_reason"],
            "terminal_event_with_valid_output",
        )
        self.assertEqual(state["tasks"]["grace"]["output_validation_state"], "valid")
        self.assertIsNotNone(state["tasks"]["grace"]["output_valid_at"])
        self.assertIsNotNone(state["tasks"]["grace"]["process_exit_at"])
        self.assertIsNone(state["tasks"]["healthy"]["terminal_event_at"])
        self.assertEqual(state["tasks"]["healthy"]["output_validation_state"], "valid")

    def test_supervisor_restart_reconciles_running_attempt_without_duplicate_retry(self) -> None:
        workflow = self.write_workflow(
            "restart-reconciliation",
            [
                {
                    "id": "work",
                    "depends_on": [],
                    "prompt": "TERMINAL-MISSING",
                    "output_schema": "schemas/out.json",
                    "retries": 1,
                }
            ],
            {"out": OBJECT_SCHEMA},
        )
        script = (
            Path(__file__).resolve().parents[1]
            / "skills" / "codex-workflows" / "scripts" / "codex_workflows.py"
        )
        environment = os.environ.copy()
        environment["CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS"] = "0.4"
        worker = subprocess.Popen(
            [
                sys.executable,
                str(script),
                "--project-root",
                str(self.project),
                "run",
                str(workflow),
                "--codex-bin",
                str(self.fake_codex),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
        )
        run_dir: Path | None = None
        for _ in range(100):
            run_files = list(self.data.glob("exec-runs/*/*/run.json"))
            if run_files:
                candidate = run_files[0].parent
                attempt_path = candidate / "tasks" / "work" / "attempt-1" / "attempt.json"
                if attempt_path.is_file():
                    attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
                    if attempt.get("terminal_event_at"):
                        run_dir = candidate
                        break
            time.sleep(0.02)
        self.assertIsNotNone(run_dir)
        status_code, status_stdout, status_stderr = self.invoke(
            "status", run_dir.name, "--json"
        )
        self.assertEqual(status_code, 0, status_stderr)
        self.assertEqual(json.loads(status_stdout)["tasks"]["work"]["attempt"], 1)
        with self.assertRaisesRegex(ContractError, "another worker already owns this run"):
            asyncio.run(execute_run(run_dir))
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=5)

        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.4"}):
            code, _, stderr = self.invoke("_worker", str(run_dir))

        self.assertEqual(code, 1, stderr)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["restart_count"], 1)
        self.assertEqual(state["tasks"]["work"]["attempt"], 2)
        starts = self.fake_log()["starts"]
        self.assertEqual([item["kind"] for item in starts], ["terminal-missing"] * 2)


class WorkflowValidationTests(ExecRunnerTestCase):
    def test_builtin_adversarial_plugin_review_validates_and_plans(self) -> None:
        install_code, _, install_stderr = self.invoke(
            "workflow", "install", "builtin:adversarial-plugin-review",
            "--name", "adversarial-plugin-review",
        )
        self.assertEqual(install_code, 0, install_stderr)
        code, stdout, stderr = self.invoke(
            "workflow", "validate", "project:adversarial-plugin-review"
        )
        self.assertEqual(code, 0, stderr)
        validated = json.loads(stdout)
        self.assertEqual(
            validated["task_order"],
            ["attack-surfaces", "challenge-findings", "release-verdict"],
        )

        plan_code, plan_stdout, plan_stderr = self.invoke(
            "plan",
            "project:adversarial-plugin-review",
            "--input",
            'objective="Review the plugin"',
            "--input",
            'review-lenses=["security","packaging","tests"]',
            "--input",
            'release-criteria="No release blockers"',
        )
        self.assertEqual(plan_code, 0, plan_stderr)
        plan = json.loads(plan_stdout)
        self.assertEqual(plan["planned_calls"], 10)
        self.assertEqual(plan["max_parallel"], 6)
        self.assertEqual(
            [task["id"] for task in plan["tasks"]],
            ["attack-surfaces", "challenge-findings", "release-verdict"],
        )
        self.assertEqual(plan["tasks"][0]["fanout_items"], 3)
        self.assertTrue(all(task["sandbox"] == "read-only" for task in plan["tasks"]))
        self.assertEqual(plan["tasks"][0]["agent"], "adversarial-reviewer")
        self.assertTrue(all(task["model"] == "gpt-5.6-sol" for task in plan["tasks"]))

    def test_non_standard_json_input_is_rejected_before_run_persistence(self) -> None:
        workflow = self.write_workflow(
            "numeric-input",
            [{"id": "work", "depends_on": [], "prompt": "VALUE {{ inputs.value }}", "output_schema": "schemas/out.json"}],
            {"out": OBJECT_SCHEMA},
            inputs={"value": "number"},
        )

        for raw in ("NaN", "1e400"):
            with self.subTest(raw=raw):
                code, _, stderr = self.invoke(
                    "run",
                    str(workflow),
                    "--input",
                    f"value={raw}",
                    "--codex-bin",
                    str(self.fake_codex),
                )

                self.assertEqual(code, 2)
                self.assertTrue(
                    "non-standard JSON constant" in stderr or "non-finite numbers" in stderr,
                    stderr,
                )
                self.assertEqual(list(self.data.glob("exec-runs/*/*/run.json")), [])

    def test_scoped_workflow_symlink_cannot_escape_scope(self) -> None:
        outside = self.root / "outside"
        outside.mkdir()
        (outside / "workflow.json").write_text("{}", encoding="utf-8")
        scope = self.project / ".codex" / "exec-workflows"
        scope.mkdir(parents=True)
        (scope / "escaped").symlink_to(outside, target_is_directory=True)

        code, _, stderr = self.invoke("workflow", "validate", "project:escaped")

        self.assertEqual(code, 2)
        self.assertIn("symlink aliasing is not allowed", stderr)

    def test_cycle_is_rejected(self) -> None:
        workflow = self.write_workflow(
            "cycle",
            [
                {
                    "id": "alpha",
                    "depends_on": ["beta"],
                    "prompt": "alpha",
                    "output_schema": "schemas/out.json",
                },
                {
                    "id": "beta",
                    "depends_on": ["alpha"],
                    "prompt": "beta",
                    "output_schema": "schemas/out.json",
                },
            ],
            {"out": OBJECT_SCHEMA},
        )

        with self.assertRaisesRegex(ContractError, "contains a cycle"):
            load_workflow(workflow / "workflow.json")

    def test_output_schema_parent_traversal_is_rejected(self) -> None:
        workflow = self.write_workflow(
            "path-traversal",
            [
                {
                    "id": "alpha",
                    "depends_on": [],
                    "prompt": "alpha",
                    "output_schema": "../outside.json",
                }
            ],
            {"unused": OBJECT_SCHEMA},
        )
        (workflow.parent / "outside.json").write_text(
            json.dumps(OBJECT_SCHEMA), encoding="utf-8"
        )

        with self.assertRaisesRegex(ContractError, "must be workflow-relative"):
            load_workflow(workflow / "workflow.json")

    def test_reference_to_non_dependency_is_rejected(self) -> None:
        workflow = self.write_workflow(
            "invalid-reference",
            [
                {
                    "id": "source",
                    "depends_on": [],
                    "prompt": "source",
                    "output_schema": "schemas/out.json",
                },
                {
                    "id": "consumer",
                    "depends_on": [],
                    "prompt": "consume {{ tasks.source.output }}",
                    "output_schema": "schemas/out.json",
                },
            ],
            {"out": OBJECT_SCHEMA},
        )

        with self.assertRaisesRegex(ContractError, "source.*upstream dependency"):
            load_workflow(workflow / "workflow.json")

    def test_output_field_typo_is_rejected_before_execution(self) -> None:
        workflow = self.write_workflow(
            "output-typo",
            [
                {
                    "id": "source",
                    "depends_on": [],
                    "prompt": "source",
                    "output_schema": "schemas/out.json",
                },
                {
                    "id": "consumer",
                    "depends_on": ["source"],
                    "prompt": "consume {{ tasks.source.output.typo }}",
                    "output_schema": "schemas/out.json",
                },
            ],
            {"out": OBJECT_SCHEMA},
        )

        with self.assertRaisesRegex(ContractError, "does not exist.*typo"):
            load_workflow(workflow / "workflow.json")

    def test_nested_primitive_input_path_is_rejected_at_load(self) -> None:
        workflow = self.write_workflow(
            "primitive-input-path",
            [
                {
                    "id": "work",
                    "depends_on": [],
                    "prompt": "{{ inputs.request.typo }}",
                    "output_schema": "schemas/out.json",
                }
            ],
            {"out": OBJECT_SCHEMA},
            inputs={"request": "string"},
        )

        with self.assertRaisesRegex(ContractError, "cannot select a nested path from string"):
            load_workflow(workflow / "workflow.json")

    def test_nested_object_input_path_is_resolved_during_plan(self) -> None:
        workflow = self.write_workflow(
            "object-input-path",
            [
                {
                    "id": "work",
                    "depends_on": [],
                    "prompt": "{{ inputs.request.missing }}",
                    "output_schema": "schemas/out.json",
                }
            ],
            {"out": OBJECT_SCHEMA},
            inputs={"request": "object"},
        )

        code, _, stderr = self.invoke(
            "plan", str(workflow), "--input", 'request={"present":true}'
        )

        self.assertEqual(code, 2)
        self.assertIn("cannot resolve", stderr)

    def test_unsupported_schema_keywords_are_rejected(self) -> None:
        invalid_schema = {
            "type": "object",
            "properties": {"ok": {"type": "string", "minLength": 1}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        workflow = self.write_workflow(
            "unsupported-schema",
            [
                {
                    "id": "work",
                    "depends_on": [],
                    "prompt": "work",
                    "output_schema": "schemas/out.json",
                }
            ],
            {"out": invalid_schema},
        )

        with self.assertRaisesRegex(ContractError, "unsupported schema keywords: minLength"):
            load_workflow(workflow / "workflow.json")


if __name__ == "__main__":
    unittest.main()
