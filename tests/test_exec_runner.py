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
    if prompt.startswith("METHOD-SELECTION"):
        if "SELECTOR-FAIL" in prompt:
            return {"kind": "selector-fail"}
        if "INVALID-SELECTION" in prompt:
            return {"kind": "selector-invalid"}
        method = "direct"
        for marker, selected in (
            ("SELECT-ADAPTIVE", "adaptive-deepening"),
            ("SELECT-GRAPH", "graph-completion"),
            ("SELECT-HYBRID", "hybrid"),
        ):
            if marker in prompt:
                method = selected
        return {"kind": "selector", "method": method}
    for prefix, kind in (
        ("PROMPT-DIRECT-ANSWER", "prompt-direct"),
        ("PROMPT-BASELINE", "prompt-baseline"),
        ("PROMPT-GAP-DETECTION", "prompt-gaps"),
        ("PROMPT-ENRICH", "prompt-enrich"),
        ("PROMPT-VALIDATION", "prompt-validation"),
        ("PROMPT-CRITIQUE", "prompt-critique"),
        ("PROMPT-OWNER", "prompt-owner"),
    ):
        if prompt.startswith(prefix):
            return {"kind": kind}
    match = re.search(r"FANOUT index=(\d+) item=(-?\d+)", prompt)
    if match:
        return {"kind": "fanout", "index": int(match.group(1))}
    if prompt.startswith("SYNTH "):
        return {"kind": "synthesis"}
    if prompt.startswith("FAIL"):
        return {"kind": "failure"}
    if prompt.startswith("MALFORMED"):
        return {"kind": "malformed"}
    match = re.search(r"LOOP-DISCOVER cursor=([^\n]*)", prompt)
    if match:
        return {"kind": "loop-discover", "cursor": match.group(1).strip()}
    if prompt.startswith("LOOP-PROCESS"):
        return {"kind": "loop-process"}
    if prompt.startswith("Read GitHub issues matching"):
        match = re.search(r"Cursor: ([^\n]*)", prompt)
        return {"kind": "github-discover", "cursor": match.group(1).strip() if match else "0"}
    if prompt.startswith("Triage this GitHub issue"):
        return {"kind": "github-triage"}
    if prompt.startswith("Produce a read-only operator report"):
        return {"kind": "github-report"}
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
    elif identity["kind"] == "selector-invalid":
        final_path = Path(option("--output-last-message"))
        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_text("{invalid selection", encoding="utf-8")
        raise SystemExit(0)
    elif identity["kind"] == "selector-fail":
        raise SystemExit(9)
    elif identity["kind"] == "selector":
        output = {
            "method": identity["method"],
            "objective": "Fixture objective",
            "consumer": "Repository operator",
            "decision_or_target_query": "Resolve the fixture target query",
            "required_output": "Evidence-backed Markdown and strict JSON",
            "minimum_inputs": ["objective", "project root", "operational bounds"],
            "source_constraints": ["Use fixture sources"],
            "tool_constraints": ["Use read-only tools"],
            "costly_if_wrong": ["Ownership mapping"],
            "quality_threshold": "Every load-bearing claim has evidence",
            "stop_rule": "Stop when high-impact gaps are resolved or unresolved",
            "rationale": "Selected from the installed methodology contract",
        }
    elif identity["kind"] == "prompt-direct":
        if "SLOW-DEADLINE" in prompt:
            time.sleep(60)
        output = {
            "synthesis": "Direct fixture answer",
            "evidence_refs": ["fixture:direct"],
            "limitations": [],
        }
    elif identity["kind"] == "prompt-baseline":
        output = {
            "synthesis": "Baseline fixture synthesis",
            "risky_claims": ["Fixture claim"],
            "candidate_facts": [],
            "limitations": [],
        }
    elif identity["kind"] == "prompt-gaps":
        output = {
            "gaps": [{
                "id": "gap-1",
                "description": "Resolve the fixture gap",
                "affected_claim": "Fixture claim",
                "impact": "high",
                "status": "open",
                "expected_value_score": 10,
                "estimated_cost_calls": 1,
                "method": "graph-completion" if "SELECT-GRAPH" in prompt else "adaptive-deepening",
                "method_card": "Inspect fixture evidence and validate provenance",
                "stop_condition": "One independent source or explicit unresolved status",
                "evidence_refs": [],
            }]
        }
    elif identity["kind"] == "prompt-enrich":
        output = {
            "gap_id": "initial-objective" if "initial-objective" in prompt else "gap-1",
            "summary": "Fixture evidence packet",
            "evidence": [{
                "claim": "Fixture claim",
                "source_ref": "fixture:source",
                "location": "fixture:1",
                "observed_at": "2026-08-20T00:00:00Z",
                "independent": True,
            }],
            "candidate_facts": [{
                "id": "fact-1",
                "subject": "service-a",
                "predicate": "owned-by",
                "object": "team-a",
                "status": "candidate",
                "sources": [{
                    "ref": "fixture:source",
                    "location": "fixture:1",
                    "observed_at": "2026-08-20T00:00:00Z",
                    "independent": True,
                }],
                "valid_time": "2026-08-20",
                "confidence_reason": "Primary fixture evidence",
            }],
            "limitations": [],
        }
    elif identity["kind"] == "prompt-validation":
        output = {
            "facts": [{
                "id": "fact-1",
                "subject": "service-a",
                "predicate": "owned-by",
                "object": "team-a",
                "status": "candidate",
                "sources": [{
                    "ref": "fixture:source",
                    "location": "fixture:1",
                    "observed_at": "2026-08-20T00:00:00Z",
                    "independent": True,
                }],
                "valid_time": "2026-08-20",
                "confidence_reason": "Validated fixture provenance",
            }],
            "issues": [],
        }
    elif identity["kind"] == "prompt-critique":
        if "CRITIQUE-FAIL" in prompt:
            raise SystemExit(9)
        output = {"discrepancies": [], "unresolved_conflicts": []}
    elif identity["kind"] == "prompt-owner":
        wave_match = re.search(r"Wave: (\d+)", prompt)
        wave = int(wave_match.group(1)) if wave_match else 1
        continue_wave = ("TWO-WAVES" in prompt or "BUDGET-STOP" in prompt) and wave == 1
        conflicted = "UNRESOLVED-CONFLICT" in prompt
        gaps = [] if "SELECT-DIRECT" in prompt else [{
            "id": "gap-1",
            "description": "Resolve the fixture gap",
            "affected_claim": "Fixture claim",
            "impact": "high",
            "status": "open" if continue_wave else ("unresolved" if conflicted else "resolved"),
            "expected_value_score": 10,
            "estimated_cost_calls": 1,
            "method": "graph-completion" if "SELECT-GRAPH" in prompt else "adaptive-deepening",
            "method_card": "Inspect fixture evidence and validate provenance",
            "stop_condition": "One independent source or explicit unresolved status",
            "evidence_refs": ["fixture:source"],
        }]
        facts = [] if "SELECT-DIRECT" in prompt else [{
            "id": "fact-1",
            "subject": "service-a",
            "predicate": "owned-by",
            "object": "team-a",
            "status": "conflicted" if conflicted else "accepted",
            "sources": [{
                "ref": "fixture:source",
                "location": "fixture:1",
                "observed_at": "2026-08-20T00:00:00Z",
                "independent": True,
            }],
            "valid_time": "2026-08-20",
            "confidence_reason": "Owner reviewed fixture provenance",
        }]
        output = {
            "synthesis": "Owner fixture synthesis",
            "gaps": gaps,
            "graph_facts": facts,
            "conflicts": ["Fixture conflict remains unresolved"] if conflicted else [],
            "limitations": ["Fixture limitation"] if conflicted else [],
            "next_wave_gap_ids": ["gap-1"] if continue_wave else [],
            "wave_change_summary": f"Completed fixture wave {wave}",
            "stop": {
                "should_stop": not continue_wave,
                "reason": "fixture-complete" if not continue_wave else "continue-high-impact-gap",
                "rationale": "Fixture owner gate",
            },
        }
    elif identity["kind"] == "loop-discover":
        cursor = str(identity.get("cursor") or "0")
        next_cursor = str(int(cursor) + 1)
        output = {
            "items": [{"id": "issue-1", "updated_at": "1"}],
            "next_cursor": next_cursor,
        }
    elif identity["kind"] == "loop-process":
        output = {"ok": True}
    elif identity["kind"] == "github-discover":
        cursor = str(identity.get("cursor") or "0")
        output = {
            "issues": [{
                "number": 7,
                "title": "Fixture issue",
                "body": "Fixture body",
                "url": "https://example.invalid/issues/7",
                "updated_at": "2026-08-20T00:00:00Z",
            }],
            "next_cursor": str(int(cursor) + 1),
        }
    elif identity["kind"] == "github-triage":
        output = {
            "issue_number": 7,
            "classification": "bug",
            "summary": "Fixture triage",
            "recommended_action": "Review manually",
            "authorized_mutation": False,
        }
    elif identity["kind"] == "github-report":
        output = {
            "status": "completed",
            "triaged": 1,
            "summary": "Fixture report",
            "unauthorized_mutations_blocked": True,
        }
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

LOOP_DISCOVERY_SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "updated_at": {"type": "string"},
                },
                "required": ["id", "updated_at"],
                "additionalProperties": False,
            },
        },
        "next_cursor": {"type": "string"},
    },
    "required": ["items", "next_cursor"],
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
        loop: dict[str, object] | None = None,
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
        if loop is not None:
            workflow["loop"] = loop
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


class LoopWorkflowTests(ExecRunnerTestCase):
    def loop_config(self, *, max_failures: int = 3) -> dict[str, object]:
        return {
            "mode": "until-cancelled",
            "interval_seconds": 5,
            "jitter_seconds": 0,
            "backoff": "exponential",
            "max_backoff_seconds": 60,
            "max_calls_per_cycle": 10,
            "max_cycle_seconds": 30,
            "max_consecutive_failures": max_failures,
            "cursor": "tasks.discover.output.next_cursor",
            "cursor_input": "cursor",
            "instance_key": "fixture:{{ inputs.source }}",
            "retain_cycles": 5,
            "permissions": {},
        }

    def write_loop(self, name: str, *, failing_tail: bool = False, max_failures: int = 3) -> Path:
        tasks: list[dict[str, object]] = [
            {
                "id": "discover",
                "depends_on": [],
                "prompt": "LOOP-DISCOVER cursor={{ inputs.cursor }}",
                "output_schema": "schemas/discovery.json",
                "sandbox": "read-only",
            },
            {
                "id": "process",
                "depends_on": ["discover"],
                "foreach": "tasks.discover.output.items",
                "item_name": "issue",
                "idempotency_key": "{{ issue.id }}:{{ issue.updated_at }}",
                "max_items": 3,
                "prompt": "LOOP-PROCESS {{ issue.id }}:{{ issue.updated_at }}",
                "output_schema": "schemas/out.json",
                "sandbox": "read-only",
            },
        ]
        if failing_tail:
            tasks.append(
                {
                    "id": "finish",
                    "depends_on": ["process"],
                    "prompt": "FAIL cycle after durable item completion",
                    "output_schema": "schemas/out.json",
                    "sandbox": "read-only",
                }
            )
        return self.write_workflow(
            name,
            tasks,
            {"discovery": LOOP_DISCOVERY_SCHEMA, "out": OBJECT_SCHEMA},
            inputs={"cursor": "string", "source": "string"},
            loop=self.loop_config(max_failures=max_failures),
        )

    def test_loop_contract_plan_and_write_isolation_validation(self) -> None:
        workflow = self.write_loop("loop-plan")

        code, stdout, stderr = self.invoke(
            "plan",
            str(workflow),
            "--input",
            'cursor="0"',
            "--input",
            'source="fixture"',
        )

        self.assertEqual(code, 0, stderr)
        plan = json.loads(stdout)
        self.assertIsNone(plan["planned_calls"])
        self.assertTrue(plan["loop"]["persistent"])
        self.assertEqual(plan["loop"]["planned_calls_per_cycle"], 4)
        self.assertEqual(
            plan["loop"]["cost_model"]["total_calls"],
            "unbounded-until-cancelled",
        )
        self.assertEqual(plan["loop"]["permissions"]["close_issues"], False)

        raw = json.loads((workflow / "workflow.json").read_text(encoding="utf-8"))
        raw["tasks"][1]["sandbox"] = "workspace-write"
        (workflow / "workflow.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "git-worktree"):
            load_workflow(workflow / "workflow.json", self.project)
        raw["tasks"][1]["sandbox"] = "read-only"
        raw["tasks"][1].pop("idempotency_key")
        (workflow / "workflow.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "require an idempotency key"):
            load_workflow(workflow / "workflow.json", self.project)

    def test_bundled_monitors_validate_install_and_deny_mutations(self) -> None:
        for name in ("loop-monitor", "github-issue-worker"):
            with self.subTest(name=name):
                validate_code, _, validate_stderr = self.invoke(
                    "workflow", "validate", f"builtin:{name}"
                )
                self.assertEqual(validate_code, 0, validate_stderr)
        inputs = (
            Path(__file__).resolve().parent.parent
            / "skills/codex-workflows/assets/workflows/github-issue-worker/example-inputs.json"
        )
        plan_code, plan_stdout, plan_stderr = self.invoke(
            "plan", "builtin:github-issue-worker", "--inputs", str(inputs)
        )
        self.assertEqual(plan_code, 0, plan_stderr)
        plan = json.loads(plan_stdout)
        self.assertTrue(all(value is False for value in plan["loop"]["permissions"].values()))
        self.assertTrue(all(task["sandbox"] == "read-only" for task in plan["tasks"]))

        install_code, _, install_stderr = self.invoke(
            "workflow",
            "install",
            "builtin:github-issue-worker",
            "--name",
            "project-issue-worker",
            "--json",
        )
        self.assertEqual(install_code, 0, install_stderr)
        installed_code, _, installed_stderr = self.invoke(
            "workflow", "validate", "project:project-issue-worker"
        )
        self.assertEqual(installed_code, 0, installed_stderr)
        self.assertTrue(
            (
                self.project
                / ".codex/exec-workflows/project-issue-worker/example-inputs.json"
            ).is_file()
        )
        installed_root = (
            self.project / ".codex/exec-workflows/project-issue-worker"
        )
        self.assertEqual(
            sorted(
                path.relative_to(installed_root).as_posix()
                for path in installed_root.rglob("*")
                if path.is_file()
            ),
            [
                "example-inputs.json",
                "schemas/issues.json",
                "schemas/report.json",
                "schemas/triage.json",
                "workflow.json",
            ],
        )

    def test_persistent_writer_runs_in_isolated_git_worktree(self) -> None:
        subprocess.run(
            ["git", "-C", str(self.project), "init", "-q"], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.email", "test@example.invalid"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.project), "config", "user.name", "Test"], check=True
        )
        subprocess.run(
            ["git", "-C", str(self.project), "commit", "--allow-empty", "-qm", "initial"],
            check=True,
        )
        workflow = self.write_loop("loop-writer")
        raw = json.loads((workflow / "workflow.json").read_text(encoding="utf-8"))
        raw["tasks"][1].update(
            {"sandbox": "workspace-write", "write_isolation": "git-worktree"}
        )
        (workflow / "workflow.json").write_text(json.dumps(raw), encoding="utf-8")
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                str(workflow),
                "--input",
                'cursor="0"',
                "--input",
                'source="fixture"',
                "--codex-bin",
                str(self.fake_codex),
                "--allow-workspace-write",
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        implementation = sys.modules["workflow_governor._exec_runner_impl"]

        async def pause_after_cycle(target: Path, _wake_at: object) -> str:
            implementation._atomic_json(
                target / "control.json",
                {"desired_status": "paused", "updated_at": implementation.utc_now()},
            )
            return "paused"

        with patch(
            "workflow_governor._exec_runner_impl._wait_for_loop_wake",
            new=pause_after_cycle,
        ):
            self.assertEqual(asyncio.run(execute_run(run_dir)), 0)
        worktree = run_dir / "cycles" / "000001" / "worktree"
        self.assertTrue((worktree / ".git").exists())
        process_call = next(
            call for call in self.fake_log()["calls"] if call["identity"]["kind"] == "loop-process"
        )
        cd_index = process_call["argv"].index("--cd")
        self.assertEqual(Path(process_call["argv"][cd_index + 1]), worktree)
        instructions = [
            value
            for index, value in enumerate(process_call["argv"])
            if process_call["argv"][index - 1 : index] == ["--config"]
        ]
        self.assertTrue(any("Allowed external mutations: none" in value for value in instructions))

    def test_github_issue_worker_fixture_triages_update_exactly_once(self) -> None:
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                "builtin:github-issue-worker",
                "--input",
                'cursor="0"',
                "--input",
                'query="fixture"',
                "--input",
                'repository="owner/repository"',
                "--codex-bin",
                str(self.fake_codex),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        implementation = sys.modules["workflow_governor._exec_runner_impl"]
        waits = 0

        async def two_cycles_then_pause(target: Path, _wake_at: object) -> str:
            nonlocal waits
            waits += 1
            if waits >= 2:
                implementation._atomic_json(
                    target / "control.json",
                    {"desired_status": "paused", "updated_at": implementation.utc_now()},
                )
                return "paused"
            return "running"

        with patch(
            "workflow_governor._exec_runner_impl._wait_for_loop_wake",
            new=two_cycles_then_pause,
        ):
            self.assertEqual(asyncio.run(execute_run(run_dir)), 0)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["checkpoint"]["cursor"], "2")
        kinds = [entry["kind"] for entry in self.fake_log()["starts"]]
        self.assertEqual(kinds.count("github-discover"), 2)
        self.assertEqual(kinds.count("github-triage"), 1)
        self.assertEqual(kinds.count("github-report"), 2)
        self.assertTrue(
            all(
                call["argv"][call["argv"].index("--sandbox") + 1] == "read-only"
                for call in self.fake_log()["calls"]
            )
        )

    def test_loop_cycles_checkpoint_deduplicate_and_rebuild_projection(self) -> None:
        workflow = self.write_loop("loop-state")
        raw_workflow = json.loads(
            (workflow / "workflow.json").read_text(encoding="utf-8")
        )
        raw_workflow["loop"]["retain_cycles"] = 1
        (workflow / "workflow.json").write_text(
            json.dumps(raw_workflow), encoding="utf-8"
        )
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                str(workflow),
                "--input",
                'cursor="0"',
                "--input",
                'source="fixture"',
                "--codex-bin",
                str(self.fake_codex),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        implementation = sys.modules["workflow_governor._exec_runner_impl"]
        waits = 0

        async def two_cycles_then_pause(target: Path, _wake_at: object) -> str:
            nonlocal waits
            waits += 1
            if waits >= 2:
                implementation._atomic_json(
                    target / "control.json",
                    {"desired_status": "paused", "updated_at": implementation.utc_now()},
                )
                return "paused"
            return "running"

        with patch(
            "workflow_governor._exec_runner_impl._wait_for_loop_wake",
            new=two_cycles_then_pause,
        ):
            worker_code = asyncio.run(execute_run(run_dir))

        self.assertEqual(worker_code, 0)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "paused")
        self.assertEqual(state["last_completed_cycle_id"], 2)
        self.assertEqual(state["checkpoint"]["cursor"], "2")
        kinds = [entry["kind"] for entry in self.fake_log()["starts"]]
        self.assertEqual(kinds.count("loop-discover"), 2)
        self.assertEqual(kinds.count("loop-process"), 1)
        idempotency = json.loads((run_dir / "idempotency.json").read_text(encoding="utf-8"))
        self.assertEqual(len(idempotency["entries"]["process"]), 1)
        self.assertEqual(
            [path.name for path in (run_dir / "cycles").iterdir() if path.is_dir()],
            ["000002"],
        )

        events = implementation._read_loop_events(run_dir)
        self.assertEqual(
            [event["sequence"] for event in events], list(range(1, len(events) + 1))
        )
        self.assertTrue(
            {
                "run_id",
                "loop_id",
                "cycle_id",
                "workflow_digest",
                "input_digest",
                "output_digest",
                "task_summary",
                "call_usage",
                "next_wake_at",
                "event_digest",
            }.issubset(events[-1])
        )
        self.assertEqual(set(events[-1]["cursor"]), {"sha256"})
        projection = (run_dir / "STATE.md").read_text(encoding="utf-8")
        (run_dir / "STATE.md").unlink()
        status_code, _, status_stderr = self.invoke("status", state["run_id"], "--json")
        self.assertEqual(status_code, 0, status_stderr)
        self.assertEqual((run_dir / "STATE.md").read_text(encoding="utf-8"), projection)

        tail_code, tail_stdout, tail_stderr = self.invoke(
            "tail", state["run_id"], "--lines", "2"
        )
        self.assertEqual(tail_code, 0, tail_stderr)
        self.assertEqual(len(tail_stdout.splitlines()), 2)

        state_log = run_dir / "state.jsonl"
        original_log = state_log.read_bytes()
        lines = original_log.decode("utf-8").splitlines()
        tampered = json.loads(lines[-1])
        tampered["status"] = "running"
        lines[-1] = json.dumps(tampered, sort_keys=True)
        state_log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        digest_code, _, digest_stderr = self.invoke("status", state["run_id"])
        self.assertEqual(digest_code, 2)
        self.assertIn("invalid digest", digest_stderr)
        state_log.write_bytes(original_log)

        with (run_dir / "state.jsonl").open("ab") as handle:
            handle.write(b'{"truncated":true}')
        corrupt_code, _, corrupt_stderr = self.invoke("status", state["run_id"])
        self.assertEqual(corrupt_code, 2)
        self.assertIn("truncated tail", corrupt_stderr)

    def test_detached_loop_supervisor_continues_without_calling_agent(self) -> None:
        workflow = self.write_loop("loop-detached")
        started = time.monotonic()
        code, stdout, stderr = self.invoke(
            "run",
            str(workflow),
            "--input",
            'cursor="0"',
            "--input",
            'source="fixture"',
            "--codex-bin",
            str(self.fake_codex),
            "--detach",
        )
        self.assertEqual(code, 0, stderr)
        self.assertLess(time.monotonic() - started, 2)
        run_id = stdout.strip()
        run_dir = self.only_run_dir()

        def stop_worker() -> None:
            if not (run_dir / "run.json").is_file():
                return
            state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            if state.get("status") not in {"cancelled", "failed"}:
                self.invoke("cancel", run_id)
                for _ in range(100):
                    state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
                    if state.get("status") in {"cancelled", "failed"}:
                        break
                    time.sleep(0.02)

        self.addCleanup(stop_worker)
        for _ in range(500):
            state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            if state.get("status") == "sleeping" and state["checkpoint"]["cycle_id"] == 1:
                break
            time.sleep(0.02)
        else:
            self.fail("detached supervisor did not complete its first cycle")
        cancel_code, _, cancel_stderr = self.invoke("cancel", run_id)
        self.assertEqual(cancel_code, 0, cancel_stderr)
        for _ in range(500):
            state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            if state.get("status") == "cancelled":
                break
            time.sleep(0.02)
        else:
            self.fail("detached supervisor did not observe cancellation")
        self.assertTrue((run_dir / "STATE.md").is_file())

    def test_partial_cycle_failure_reuses_item_and_opens_circuit(self) -> None:
        workflow = self.write_loop("loop-circuit", failing_tail=True, max_failures=2)
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                str(workflow),
                "--input",
                'cursor="0"',
                "--input",
                'source="fixture"',
                "--codex-bin",
                str(self.fake_codex),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()

        async def immediate(_target: Path, _wake_at: object) -> str:
            return "running"

        with patch(
            "workflow_governor._exec_runner_impl._wait_for_loop_wake",
            new=immediate,
        ):
            worker_code = asyncio.run(execute_run(run_dir))

        self.assertEqual(worker_code, 1)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "circuit-open")
        self.assertEqual(state["consecutive_failures"], 2)
        self.assertEqual(state["checkpoint"]["cycle_id"], 0)
        kinds = [entry["kind"] for entry in self.fake_log()["starts"]]
        self.assertEqual(kinds.count("loop-process"), 1)
        self.assertEqual(kinds.count("failure"), 2)

    def test_cycle_timeout_stops_process_group_and_opens_circuit(self) -> None:
        loop = self.loop_config(max_failures=1)
        loop["max_cycle_seconds"] = 1
        workflow = self.write_workflow(
            "loop-timeout",
            [
                {
                    "id": "discover",
                    "depends_on": [],
                    "prompt": "TERMINAL-DESCENDANT",
                    "output_schema": "schemas/discovery.json",
                    "sandbox": "read-only",
                }
            ],
            {"discovery": LOOP_DISCOVERY_SCHEMA},
            inputs={"cursor": "string", "source": "string"},
            loop=loop,
        )
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                str(workflow),
                "--input",
                'cursor="0"',
                "--input",
                'source="fixture"',
                "--codex-bin",
                str(self.fake_codex),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        started = time.monotonic()
        self.assertEqual(asyncio.run(execute_run(run_dir)), 1)
        self.assertLess(time.monotonic() - started, 4)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "circuit-open")
        self.assertEqual(state["error"], "cycle timeout")
        child_pids = self.fake_log().get("child_pids", [])
        self.assertEqual(len(child_pids), 1)
        for _ in range(100):
            if not process_is_live(child_pids[0]):
                break
            time.sleep(0.02)
        self.assertFalse(process_is_live(child_pids[0]))

    def test_interrupted_supervisor_resumes_from_committed_cursor(self) -> None:
        workflow = self.write_loop("loop-restart")
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                str(workflow),
                "--input",
                'cursor="0"',
                "--input",
                'source="fixture"',
                "--codex-bin",
                str(self.fake_codex),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()

        async def interrupt_after_checkpoint() -> None:
            worker = asyncio.create_task(execute_run(run_dir))
            for _ in range(200):
                state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
                if state.get("status") == "sleeping" and state["checkpoint"]["cycle_id"] == 1:
                    break
                await asyncio.sleep(0.01)
            else:
                self.fail("loop did not commit its first checkpoint")
            worker.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await worker

        asyncio.run(interrupt_after_checkpoint())
        interrupted = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(interrupted["status"], "failed")
        self.assertEqual(interrupted["checkpoint"]["cursor"], "1")

        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            resume_code, _, resume_stderr = self.invoke("resume", interrupted["run_id"])
        self.assertEqual(resume_code, 0, resume_stderr)
        implementation = sys.modules["workflow_governor._exec_runner_impl"]

        async def pause_after_cycle(target: Path, _wake_at: object) -> str:
            implementation._atomic_json(
                target / "control.json",
                {"desired_status": "paused", "updated_at": implementation.utc_now()},
            )
            return "paused"

        with patch(
            "workflow_governor._exec_runner_impl._wait_for_loop_wake",
            new=pause_after_cycle,
        ):
            self.assertEqual(asyncio.run(execute_run(run_dir)), 0)
        resumed = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(resumed["checkpoint"]["cursor"], "2")
        prompts = [entry["prompt"] for entry in self.fake_log()["calls"]]
        self.assertTrue(any(prompt.startswith("LOOP-DISCOVER cursor=0") for prompt in prompts))
        self.assertTrue(any(prompt.startswith("LOOP-DISCOVER cursor=1") for prompt in prompts))
        kinds = [entry["kind"] for entry in self.fake_log()["starts"]]
        self.assertEqual(kinds.count("loop-process"), 1)

    def test_instance_collision_and_lifecycle_commands_are_race_safe(self) -> None:
        workflow = self.write_loop("loop-instance")
        arguments = (
            "run",
            str(workflow),
            "--input",
            'cursor="0"',
            "--input",
            'source="fixture"',
            "--codex-bin",
            str(self.fake_codex),
            "--detach",
        )
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            first_code, _, first_stderr = self.invoke(*arguments)
            second_code, _, second_stderr = self.invoke(*arguments)
        self.assertEqual(first_code, 0, first_stderr)
        self.assertEqual(second_code, 2)
        self.assertIn("already owns instance", second_stderr)
        run_dir = self.only_run_dir()
        run_id = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))["run_id"]

        pause_code, _, pause_stderr = self.invoke("pause", run_id)
        self.assertEqual(pause_code, 0, pause_stderr)
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            resume_code, _, resume_stderr = self.invoke("resume", run_id)
            duplicate_resume_code, _, duplicate_resume_stderr = self.invoke(
                "resume", run_id
            )
        self.assertEqual(resume_code, 0, resume_stderr)
        self.assertEqual(duplicate_resume_code, 2)
        self.assertIn("already queued", duplicate_resume_stderr)
        cancel_code, _, cancel_stderr = self.invoke("cancel", run_id)
        self.assertEqual(cancel_code, 0, cancel_stderr)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "cancelled")


class PromptWorkflowTests(ExecRunnerTestCase):
    def prompt_arguments(self, objective: str) -> tuple[str, ...]:
        return (
            "--prompt",
            objective,
            "--codex-bin",
            str(self.fake_codex),
            "--max-waves",
            "3",
            "--max-calls-per-wave",
            "10",
            "--max-total-calls",
            "30",
            "--retries",
            "0",
            "--deadline",
            "5m",
        )

    def only_prompt_run_dir(self) -> Path:
        run_files = list(self.data.glob("prompt-runs/*/*/run.json"))
        self.assertEqual(len(run_files), 1, run_files)
        return run_files[0].parent

    def test_prompt_plan_selects_each_method_and_discloses_pins_bounds_permissions(self) -> None:
        cases = {
            "SELECT-DIRECT": "direct",
            "SELECT-ADAPTIVE": "adaptive-deepening",
            "SELECT-GRAPH": "graph-completion",
            "SELECT-HYBRID": "hybrid",
        }
        for marker, method in cases.items():
            with self.subTest(method=method):
                code, stdout, stderr = self.invoke(
                    "prompt-plan", *self.prompt_arguments(marker)
                )
                self.assertEqual(code, 0, stderr)
                plan = json.loads(stdout)
                self.assertEqual(plan["selection"]["method"], method)
                self.assertTrue(plan["selection"]["rationale"])
                self.assertEqual(plan["permissions"]["sandbox"], "read-only")
                self.assertFalse(plan["permissions"]["workspace_write"])
                self.assertEqual(plan["cost_model"]["selection_calls"], 1)
                self.assertLessEqual(plan["cost_model"]["first_wave_calls"], 10)
                self.assertEqual(
                    set(plan["methodology_pins"]["skills"]),
                    {"adaptive-deepening", "graph-completion"},
                )
                self.assertTrue(
                    all(
                        len(pin["sha256"]) == 64
                        for pin in plan["methodology_pins"]["skills"].values()
                    )
                )
                task_ids = [task["id"] for task in plan["first_wave"]["workflow"]["tasks"]]
                self.assertIn("critique", task_ids)
                self.assertEqual(task_ids[-1], "owner")

    def test_invalid_selection_and_write_request_fail_before_run(self) -> None:
        code, _, stderr = self.invoke(
            "prompt-plan", *self.prompt_arguments("INVALID-SELECTION")
        )
        self.assertEqual(code, 2)
        self.assertIn("Expecting property name", stderr)
        failed_code, _, failed_stderr = self.invoke(
            "prompt-plan", *self.prompt_arguments("SELECTOR-FAIL")
        )
        self.assertEqual(failed_code, 2)
        self.assertIn("codex exec exited with 9", failed_stderr)
        write_args = list(self.prompt_arguments("SELECT-DIRECT"))
        write_args.extend(["--sandbox", "workspace-write"])
        write_code, _, write_stderr = self.invoke("prompt-run", *write_args)
        self.assertEqual(write_code, 2)
        self.assertIn("read-only", write_stderr)
        self.assertEqual(list(self.data.glob("prompt-runs/*/*/run.json")), [])

    def test_direct_prompt_run_result_and_explicit_template_save(self) -> None:
        code, stdout, stderr = self.invoke(
            "prompt-run", *self.prompt_arguments("SELECT-DIRECT repository lookup")
        )
        self.assertEqual(code, 0, stderr)
        run_id = stdout.split()[0]
        run_dir = self.only_prompt_run_dir()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["stop_reason"], "direct-complete")
        self.assertEqual(state["completed_waves"], 1)
        self.assertTrue((run_dir / "result.md").is_file())
        self.assertTrue((run_dir / "result.json").is_file())
        self.assertTrue((run_dir / "state.jsonl").is_file())
        self.assertTrue((run_dir / "STATE.md").is_file())

        result_code, result_stdout, result_stderr = self.invoke(
            "prompt-result", run_id, "--json"
        )
        self.assertEqual(result_code, 0, result_stderr)
        result = json.loads(result_stdout)
        self.assertEqual(result["method_selection"]["method"], "direct")

        rejected_code, _, rejected_stderr = self.invoke(
            "prompt-save-template", run_id, "--name", "saved-prompt"
        )
        self.assertEqual(rejected_code, 2)
        self.assertIn("--reviewed", rejected_stderr)
        save_code, _, save_stderr = self.invoke(
            "prompt-save-template",
            run_id,
            "--name",
            "saved-prompt",
            "--reviewed",
        )
        self.assertEqual(save_code, 0, save_stderr)
        validate_code, _, validate_stderr = self.invoke(
            "workflow", "validate", "project:saved-prompt"
        )
        self.assertEqual(validate_code, 0, validate_stderr)

    def test_adaptive_second_wave_and_budget_stop_are_deterministic(self) -> None:
        code, stdout, stderr = self.invoke(
            "prompt-run", *self.prompt_arguments("SELECT-ADAPTIVE TWO-WAVES")
        )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_prompt_run_dir()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["completed_waves"], 2)
        self.assertEqual(len(state["wave_log"]), 2)
        self.assertEqual(len(state["graph_facts"]), 1)
        self.assertEqual(state["status"], "completed")
        events = [
            json.loads(line)
            for line in (run_dir / "state.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        compiled = [event for event in events if event["event"] == "wave.compiled"]
        self.assertEqual(compiled[0]["metadata"]["named_gaps"], ["gap-1"])

        budget_args = list(self.prompt_arguments("SELECT-ADAPTIVE BUDGET-STOP"))
        total_index = budget_args.index("--max-total-calls") + 1
        budget_args[total_index] = "11"
        budget_code, budget_stdout, budget_stderr = self.invoke(
            "prompt-run", *budget_args
        )
        self.assertEqual(budget_code, 0, budget_stderr)
        budget_run = budget_stdout.split()[0]
        budget_dir = next(
            path.parent
            for path in self.data.glob("prompt-runs/*/*/run.json")
            if path.parent.name == budget_run
        )
        budget_state = json.loads(
            (budget_dir / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(budget_state["run_id"], budget_run)
        self.assertEqual(budget_state["stop_reason"], "call-budget")
        self.assertEqual(budget_state["completed_waves"], 1)

    def test_critique_failure_and_unresolved_conflict_are_preserved(self) -> None:
        code, stdout, stderr = self.invoke(
            "prompt-run", *self.prompt_arguments("SELECT-GRAPH CRITIQUE-FAIL")
        )
        self.assertEqual(code, 1, stderr)
        run_dir = self.only_prompt_run_dir()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["stop_reason"], "critique-failed")

        conflict_code, conflict_stdout, conflict_stderr = self.invoke(
            "prompt-run", *self.prompt_arguments("SELECT-GRAPH UNRESOLVED-CONFLICT")
        )
        self.assertEqual(conflict_code, 0, conflict_stderr)
        conflict_run = conflict_stdout.split()[0]
        result_code, result_stdout, result_stderr = self.invoke(
            "prompt-result", conflict_run, "--json"
        )
        self.assertEqual(result_code, 0, result_stderr)
        result = json.loads(result_stdout)
        self.assertEqual(result["graph_facts"][0]["status"], "conflicted")
        self.assertIn("Fixture conflict remains unresolved", result["conflicts"])

    def test_prompt_run_stops_at_hard_deadline(self) -> None:
        arguments = list(self.prompt_arguments("SELECT-DIRECT SLOW-DEADLINE"))
        deadline_index = arguments.index("--deadline") + 1
        arguments[deadline_index] = "1s"
        started = time.monotonic()
        code, _, stderr = self.invoke("prompt-run", *arguments)
        self.assertEqual(code, 1, stderr)
        self.assertLess(time.monotonic() - started, 4)
        state = json.loads(
            (self.only_prompt_run_dir() / "run.json").read_text(encoding="utf-8")
        )
        self.assertEqual(state["status"], "failed")
        self.assertEqual(state["stop_reason"], "deadline")

    def test_detached_prompt_run_finishes_without_caller_polling(self) -> None:
        started = time.monotonic()
        code, stdout, stderr = self.invoke(
            "prompt-run",
            *self.prompt_arguments("SELECT-DIRECT detached"),
            "--detach",
        )
        self.assertEqual(code, 0, stderr)
        self.assertLess(time.monotonic() - started, 2)
        run_id = stdout.strip()
        run_dir = self.only_prompt_run_dir()
        for _ in range(500):
            state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
            if state["status"] in {"completed", "failed"}:
                break
            time.sleep(0.02)
        else:
            self.fail("detached prompt worker did not finish")
        self.assertEqual(state["status"], "completed", (run_dir / "worker.log").read_text())
        status_code, _, status_stderr = self.invoke("prompt-status", run_id, "--json")
        self.assertEqual(status_code, 0, status_stderr)

    def test_prompt_resume_continues_from_committed_wave_without_duplicate_work(self) -> None:
        plan_code, _, plan_stderr = self.invoke(
            "prompt-plan",
            *self.prompt_arguments("SELECT-ADAPTIVE TWO-WAVES restart"),
        )
        self.assertEqual(plan_code, 0, plan_stderr)
        with patch("workflow_governor._prompt_workflows_impl._spawn_worker"):
            code, stdout, stderr = self.invoke(
                "prompt-run",
                *self.prompt_arguments("SELECT-ADAPTIVE TWO-WAVES restart"),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_id = stdout.strip()
        run_dir = self.only_prompt_run_dir()
        implementation = sys.modules["workflow_governor._exec_runner_impl"]
        prompt_module = sys.modules["workflow_governor._prompt_workflows_impl"]
        original_compile = prompt_module._compile_wave

        def interrupt_before_second_wave(*arguments: object, **keywords: object):
            definition = Path(arguments[0])
            if definition.parent.name == "0002":
                raise OSError("simulated supervisor interruption")
            return original_compile(*arguments, **keywords)

        with patch.object(prompt_module, "_compile_wave", new=interrupt_before_second_wave):
            with self.assertRaisesRegex(OSError, "simulated supervisor interruption"):
                asyncio.run(prompt_module.execute_prompt_run(run_dir, implementation))
        interrupted = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(interrupted["status"], "failed")
        self.assertEqual(interrupted["completed_waves"], 1)
        self.assertEqual(interrupted["current_wave"], 2)
        calls_after_first = len(self.fake_log()["starts"])

        with patch("workflow_governor._prompt_workflows_impl._spawn_worker"):
            resume_code, _, resume_stderr = self.invoke("prompt-resume", run_id)
        self.assertEqual(resume_code, 0, resume_stderr)
        self.assertEqual(
            asyncio.run(prompt_module.execute_prompt_run(run_dir, implementation)), 0
        )
        resumed = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(resumed["status"], "completed")
        self.assertEqual(resumed["completed_waves"], 2)
        self.assertEqual(len(resumed["charged_waves"]), 2)
        self.assertEqual(len(self.fake_log()["starts"]) - calls_after_first, 6)
        with (run_dir / "state.jsonl").open("ab") as handle:
            handle.write(b'{"truncated":true}')
        corrupt_code, _, corrupt_stderr = self.invoke("prompt-status", run_id)
        self.assertEqual(corrupt_code, 2)
        self.assertIn("truncated tail", corrupt_stderr)


if __name__ == "__main__":
    unittest.main()
