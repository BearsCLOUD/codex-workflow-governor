from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from workflow_governor.exec_runner import main
from workflow_governor import _exec_runner_impl as implementation


SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
    "additionalProperties": False,
}

FAKE_CODEX = r'''#!/usr/bin/env python3
import json
import os
import re
import sys
from pathlib import Path

def option(name):
    index = sys.argv.index(name)
    return sys.argv[index + 1]

prompt = sys.stdin.read()
state = Path(os.environ["FAKE_CODEX_STATE"])
state.write_text(json.dumps({"argv": sys.argv[1:], "prompt": prompt}), encoding="utf-8")
if os.environ.get("FAKE_CODEX_FAIL"):
    raise SystemExit(7)
final = Path(option("--output-last-message"))
if os.environ.get("FAKE_CODEX_INVALID"):
    final.write_text("{invalid", encoding="utf-8")
elif "Author a narrow Codex custom-agent" in prompt:
    name = re.search(r"Requested name: ([a-z0-9-]+)", prompt).group(1)
    final.write_text(json.dumps({
        "name": name,
        "description": "Generated role",
        "developer_instructions": "Generated instructions",
    }), encoding="utf-8")
else:
    schema = json.loads(Path(option("--output-schema")).read_text(encoding="utf-8"))
    def sample(node):
        if "enum" in node:
            return node["enum"][0]
        kind = node.get("type")
        if kind == "object":
            return {name: sample(child) for name, child in node["properties"].items()}
        if kind == "array":
            return []
        if kind == "string":
            return "value"
        if kind == "integer":
            return 0
        if kind == "number":
            return 0.0
        if kind == "boolean":
            return True
        return None
    final.write_text(json.dumps(sample(schema)), encoding="utf-8")
raise SystemExit(0)
'''


def role_bytes(name: str, *, instructions: str = "Inspect carefully.", model: str = "gpt-5.6-sol") -> bytes:
    values = {
        "name": name,
        "description": f"Managed {name}",
        "developer_instructions": instructions,
        "model": model,
        "model_reasoning_effort": "xhigh",
        "sandbox_mode": "read-only",
    }
    return implementation._agent_toml(values)


class ProjectAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / ".git").mkdir()
        self.data = self.root / "data"
        self.fake_state = self.root / "fake-state.json"
        self.fake_codex = self.root / "fake-codex"
        self.fake_codex.write_text(FAKE_CODEX, encoding="utf-8")
        self.fake_codex.chmod(self.fake_codex.stat().st_mode | stat.S_IXUSR)
        self.environment = patch.dict(
            os.environ,
            {"PLUGIN_DATA": str(self.data), "FAKE_CODEX_STATE": str(self.fake_state)},
            clear=False,
        )
        self.environment.start()
        self.addCleanup(self.environment.stop)

    def invoke(self, *arguments: str) -> tuple[int, str, str]:
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--project-root", str(self.project), *arguments])
        return code, stdout.getvalue(), stderr.getvalue()

    def write_role(self, name: str, payload: bytes | None = None) -> Path:
        path = self.project / ".codex" / "agents" / f"{name}.toml"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload or role_bytes(name))
        return path

    def write_v2(self, name: str, role_name: str = "reviewer", payload: bytes | None = None) -> Path:
        payload = payload or role_bytes(role_name)
        self.write_role(role_name, payload)
        directory = self.project / ".codex" / "exec-workflows" / name
        (directory / "schemas").mkdir(parents=True)
        (directory / "agents").mkdir()
        (directory / "schemas" / "out.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        (directory / "agents" / f"{role_name}.toml").write_bytes(payload)
        role = tomllib.loads(payload.decode("utf-8"))
        workflow = {
            "schema_version": "codex-exec-workflow.v2",
            "workflow_id": name,
            "description": "Pinned role test",
            "max_parallel": 1,
            "agents": {
                role_name: {
                    "project_path": f".codex/agents/{role_name}.toml",
                    "snapshot_path": f"agents/{role_name}.toml",
                    "sha256": hashlib.sha256(payload).hexdigest(),
                    "model": role["model"],
                    "model_reasoning_effort": role["model_reasoning_effort"],
                    "sandbox_mode": role["sandbox_mode"],
                }
            },
            "inputs": {},
            "tasks": [{
                "id": "review",
                "depends_on": [],
                "prompt": "Review",
                "output_schema": "schemas/out.json",
                "agent": role_name,
            }],
        }
        (directory / "workflow.json").write_text(json.dumps(workflow), encoding="utf-8")
        return directory

    def spec(self, name: str, description: str = "External role") -> Path:
        path = self.root / f"{name}.json"
        path.write_text(json.dumps({
            "name": name,
            "description": description,
            "developer_instructions": f"Instructions for {name}",
        }), encoding="utf-8")
        return path

    def test_v1_digest_remains_byte_compatible_with_previous_contract(self) -> None:
        directory = self.project / ".codex" / "exec-workflows" / "legacy-digest"
        (directory / "schemas").mkdir(parents=True)
        schema_path = directory / "schemas" / "out.json"
        schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")
        raw = {
            "schema_version": "codex-exec-workflow.v1",
            "workflow_id": "legacy-digest",
            "description": "Legacy digest",
            "max_parallel": 1,
            "inputs": {},
            "tasks": [{
                "id": "review", "depends_on": [], "prompt": "Review",
                "output_schema": "schemas/out.json",
            }],
        }
        workflow_path = directory / "workflow.json"
        workflow_path.write_text(json.dumps(raw), encoding="utf-8")
        loaded = implementation.load_workflow(workflow_path, self.project)
        expected = implementation.digest_json({
            "workflow": raw,
            "schemas": {"schemas/out.json": implementation.digest_json(SCHEMA)},
        })
        self.assertEqual(implementation._workflow_digest(loaded), expected)

    def test_v2_plan_resolves_pinned_agent_and_rejects_task_overrides(self) -> None:
        workflow = self.write_v2("pinned")
        code, stdout, stderr = self.invoke("plan", str(workflow))
        self.assertEqual(code, 0, stderr)
        task = json.loads(stdout)["tasks"][0]
        self.assertEqual(task["agent"], "reviewer")
        self.assertEqual(task["model"], "gpt-5.6-sol")
        self.assertEqual(task["reasoning_effort"], "xhigh")
        self.assertEqual(task["sandbox"], "read-only")
        self.assertRegex(task["agent_sha256"], r"^[0-9a-f]{64}$")

        path = workflow / "workflow.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw["tasks"][0]["model"] = "forbidden"
        path.write_text(json.dumps(raw), encoding="utf-8")
        code, _, stderr = self.invoke("workflow", "validate", str(workflow))
        self.assertEqual(code, 2)
        self.assertIn("agent-bound tasks cannot override: model", stderr)

    def test_project_drift_snapshot_mismatch_and_pin_path_escape_fail_closed(self) -> None:
        workflow = self.write_v2("drift")
        self.write_role("reviewer", role_bytes("reviewer", instructions="Drifted"))
        code, _, stderr = self.invoke("plan", str(workflow))
        self.assertEqual(code, 2)
        self.assertIn("does not match the project agent file", stderr)

        self.write_role("reviewer", (workflow / "agents" / "reviewer.toml").read_bytes())
        snapshot = workflow / "agents" / "reviewer.toml"
        snapshot.write_bytes(role_bytes("reviewer", instructions="Snapshot drift"))
        code, _, stderr = self.invoke("workflow", "validate", str(workflow))
        self.assertEqual(code, 2)
        self.assertIn("does not match the workflow agent snapshot", stderr)

        snapshot.write_bytes(self.write_role("reviewer").read_bytes())
        raw = json.loads((workflow / "workflow.json").read_text(encoding="utf-8"))
        raw["agents"]["reviewer"]["project_path"] = "../reviewer.toml"
        (workflow / "workflow.json").write_text(json.dumps(raw), encoding="utf-8")
        code, _, stderr = self.invoke("workflow", "validate", str(workflow))
        self.assertEqual(code, 2)
        self.assertIn("must equal '.codex/agents/reviewer.toml'", stderr)

    def test_project_agent_symlink_alias_is_rejected(self) -> None:
        workflow = self.write_v2("symlink")
        agent = self.project / ".codex" / "agents" / "reviewer.toml"
        payload = agent.read_bytes()
        outside = self.project / "role-copy.toml"
        outside.write_bytes(payload)
        agent.unlink()
        agent.symlink_to(outside)
        code, _, stderr = self.invoke("workflow", "validate", str(workflow))
        self.assertEqual(code, 2)
        self.assertIn("symlink aliasing is not allowed", stderr)

    def test_execution_passes_exact_role_flags_and_snapshots_metadata(self) -> None:
        instructions = "Inspect exact evidence and do not edit."
        workflow = self.write_v2("execute", payload=role_bytes("reviewer", instructions=instructions))
        code, _, stderr = self.invoke(
            "run", str(workflow), "--codex-bin", str(self.fake_codex)
        )
        self.assertEqual(code, 0, stderr)
        call = json.loads(self.fake_state.read_text(encoding="utf-8"))
        argv = call["argv"]
        self.assertEqual(argv[argv.index("--model") + 1], "gpt-5.6-sol")
        self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        configs = [argv[index + 1] for index, item in enumerate(argv) if item == "--config"]
        self.assertIn('model_reasoning_effort="xhigh"', configs)
        self.assertIn(f"developer_instructions={json.dumps(instructions)}", configs)
        run_file = next(self.data.glob("exec-runs/*/*/run.json"))
        state = json.loads(run_file.read_text(encoding="utf-8"))
        self.assertEqual(state["agents"]["reviewer"]["model"], "gpt-5.6-sol")
        self.assertEqual(
            (run_file.parent / "workflow" / "agents" / "reviewer.toml").read_bytes(),
            (workflow / "agents" / "reviewer.toml").read_bytes(),
        )

    def test_spec_and_generator_authoring_create_update_and_collision(self) -> None:
        create_spec = self.spec("writer")
        code, stdout, stderr = self.invoke(
            "agent", "create", "writer", "--spec", str(create_spec),
            "--model", "gpt-5.6-sol", "--reasoning-effort", "xhigh",
            "--sandbox", "read-only", "--json",
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["name"], "writer")
        created = self.project / ".codex" / "agents" / "writer.toml"
        self.assertEqual(set(tomllib.loads(created.read_text(encoding="utf-8"))), implementation.AGENT_FIELDS)

        code, _, stderr = self.invoke(
            "agent", "create", "writer", "--spec", str(create_spec),
            "--model", "gpt-5.6-sol", "--reasoning-effort", "xhigh", "--sandbox", "read-only",
        )
        self.assertEqual(code, 2)
        self.assertIn("already exists", stderr)

        code, _, stderr = self.invoke(
            "agent", "create", "generated", "--generate", "Make a reviewer",
            "--model", "gpt-5.6-sol", "--reasoning-effort", "high", "--sandbox", "read-only",
            "--generator-model", "gpt-5.6-luna", "--generator-reasoning-effort", "low",
            "--codex-bin", str(self.fake_codex),
        )
        self.assertEqual(code, 0, stderr)
        generator_call = json.loads(self.fake_state.read_text(encoding="utf-8"))
        self.assertEqual(generator_call["argv"][generator_call["argv"].index("--sandbox") + 1], "read-only")
        self.assertIn("gpt-5.6-luna", generator_call["argv"])

        update_spec = self.spec("writer", "Updated role")
        code, _, stderr = self.invoke("agent", "update", "writer", "--spec", str(update_spec))
        self.assertEqual(code, 0, stderr)
        updated = tomllib.loads(created.read_text(encoding="utf-8"))
        self.assertEqual(updated["description"], "Updated role")
        self.assertEqual(updated["model_reasoning_effort"], "xhigh")

    def test_invalid_authoring_and_failed_generation_write_nothing(self) -> None:
        invalid = self.root / "invalid.json"
        invalid.write_text("{invalid", encoding="utf-8")
        code, _, stderr = self.invoke(
            "agent", "create", "invalid", "--spec", str(invalid),
            "--model", "gpt-5.6-sol", "--reasoning-effort", "high", "--sandbox", "read-only",
        )
        self.assertEqual(code, 2)
        self.assertFalse((self.project / ".codex" / "agents" / "invalid.toml").exists())

        with patch.dict(os.environ, {"FAKE_CODEX_FAIL": "1"}):
            code, _, stderr = self.invoke(
                "agent", "create", "failed", "--generate", "Fail",
                "--model", "gpt-5.6-sol", "--reasoning-effort", "high", "--sandbox", "read-only",
                "--codex-bin", str(self.fake_codex),
            )
        self.assertEqual(code, 2)
        self.assertIn("exited with 7", stderr)
        self.assertFalse((self.project / ".codex" / "agents" / "failed.toml").exists())

        with patch.dict(os.environ, {"FAKE_CODEX_INVALID": "1"}):
            code, _, stderr = self.invoke(
                "agent", "create", "bad-json", "--generate", "Invalid",
                "--model", "gpt-5.6-sol", "--reasoning-effort", "high", "--sandbox", "read-only",
                "--codex-bin", str(self.fake_codex),
            )
        self.assertEqual(code, 2)
        self.assertFalse((self.project / ".codex" / "agents" / "bad-json.toml").exists())

    def test_update_race_is_detected_before_writes(self) -> None:
        target = self.write_role("racer")
        original = target.read_bytes()

        def race(_args, _project):
            target.write_bytes(role_bytes("racer", instructions="Concurrent edit"))
            return {"name": "racer", "description": "Generated", "developer_instructions": "Generated"}

        with patch.object(implementation, "_generate_agent_spec", side_effect=race):
            code, _, stderr = self.invoke(
                "agent", "update", "racer", "--generate", "Update",
                "--codex-bin", str(self.fake_codex),
            )
        self.assertEqual(code, 2)
        self.assertIn("changed concurrently", stderr)
        self.assertNotEqual(target.read_bytes(), original)

    def test_update_and_manual_repin_cover_multiple_workflows(self) -> None:
        first = self.write_v2("first")
        second = self.write_v2("second")
        spec = self.spec("reviewer", "Repinned")
        code, _, stderr = self.invoke("agent", "update", "reviewer", "--spec", str(spec))
        self.assertEqual(code, 0, stderr)
        digest = hashlib.sha256(
            (self.project / ".codex" / "agents" / "reviewer.toml").read_bytes()
        ).hexdigest()
        for workflow in (first, second):
            raw = json.loads((workflow / "workflow.json").read_text(encoding="utf-8"))
            self.assertEqual(raw["agents"]["reviewer"]["sha256"], digest)
            self.assertEqual(
                (workflow / "agents" / "reviewer.toml").read_bytes(),
                (self.project / ".codex" / "agents" / "reviewer.toml").read_bytes(),
            )

        manual = role_bytes("reviewer", instructions="Manual edit")
        self.write_role("reviewer", manual)
        code, _, stderr = self.invoke("agent", "repin", "reviewer")
        self.assertEqual(code, 0, stderr)
        for workflow in (first, second):
            self.assertEqual((workflow / "agents" / "reviewer.toml").read_bytes(), manual)

    def test_atomic_update_rolls_back_every_preimage(self) -> None:
        workflow = self.write_v2("rollback")
        agent = self.project / ".codex" / "agents" / "reviewer.toml"
        preimages = {
            agent: agent.read_bytes(),
            workflow / "workflow.json": (workflow / "workflow.json").read_bytes(),
            workflow / "agents" / "reviewer.toml": (workflow / "agents" / "reviewer.toml").read_bytes(),
        }
        spec = self.spec("reviewer", "Will roll back")
        original_atomic = implementation._atomic_text
        calls = 0

        def fail_second(path, text):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError("injected batch failure")
            return original_atomic(path, text)

        with patch.object(implementation, "_atomic_text", side_effect=fail_second):
            code, _, stderr = self.invoke("agent", "update", "reviewer", "--spec", str(spec))
        self.assertEqual(code, 2)
        self.assertIn("injected batch failure", stderr)
        for path, payload in preimages.items():
            self.assertEqual(path.read_bytes(), payload)

    def test_bind_agent_converts_v1_and_install_detects_role_conflict(self) -> None:
        role = self.write_role("reviewer")
        workflow = self.project / ".codex" / "exec-workflows" / "legacy"
        (workflow / "schemas").mkdir(parents=True)
        (workflow / "schemas" / "out.json").write_text(json.dumps(SCHEMA), encoding="utf-8")
        (workflow / "workflow.json").write_text(json.dumps({
            "schema_version": "codex-exec-workflow.v1",
            "workflow_id": "legacy",
            "description": "Legacy",
            "max_parallel": 1,
            "inputs": {},
            "tasks": [{
                "id": "review", "depends_on": [], "prompt": "Review",
                "output_schema": "schemas/out.json", "sandbox": "workspace-write",
            }],
        }), encoding="utf-8")
        code, _, stderr = self.invoke(
            "workflow", "bind-agent", "project:legacy", "--task", "review", "--agent", "reviewer"
        )
        self.assertEqual(code, 0, stderr)
        raw = json.loads((workflow / "workflow.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["schema_version"], "codex-exec-workflow.v2")
        self.assertEqual(raw["tasks"][0]["agent"], "reviewer")
        self.assertNotIn("sandbox", raw["tasks"][0])
        self.assertEqual((workflow / "agents" / "reviewer.toml").read_bytes(), role.read_bytes())

        conflicting = role_bytes("adversarial-reviewer", instructions="Conflict")
        other = self.write_v2("other-review", "adversarial-reviewer", conflicting)
        code, _, stderr = self.invoke(
            "workflow", "install", "builtin:adversarial-plugin-review", "--name", "review-template"
        )
        self.assertEqual(code, 2)
        self.assertIn("conflicts with the bundled role", stderr)
        self.assertFalse((self.project / ".codex" / "exec-workflows" / "review-template").exists())

        code, _, stderr = self.invoke(
            "workflow", "install", "builtin:adversarial-plugin-review", "--name", "review-template",
            "--replace-agents",
        )
        self.assertEqual(code, 0, stderr)
        for reference in ("project:other-review", "project:review-template"):
            code, _, stderr = self.invoke("workflow", "validate", reference)
            self.assertEqual(code, 0, stderr)
        self.assertNotEqual((other / "agents" / "adversarial-reviewer.toml").read_bytes(), conflicting)

    def test_register_dry_run_and_installed_template_smoke(self) -> None:
        source = self.root / "imported.toml"
        source.write_bytes(role_bytes("imported"))
        code, stdout, stderr = self.invoke(
            "agent", "register", str(source), "--dry-run", "--json"
        )
        self.assertEqual(code, 0, stderr)
        self.assertTrue(json.loads(stdout)["dry_run"])
        self.assertFalse((self.project / ".codex" / "agents" / "imported.toml").exists())
        code, _, stderr = self.invoke("agent", "register", str(source))
        self.assertEqual(code, 0, stderr)

        code, _, stderr = self.invoke(
            "workflow", "install", "builtin:adversarial-plugin-review", "--name", "smoke-review"
        )
        self.assertEqual(code, 0, stderr)
        code, _, stderr = self.invoke("workflow", "validate", "project:smoke-review")
        self.assertEqual(code, 0, stderr)
        inputs = (
            Path(__file__).resolve().parents[1]
            / "skills" / "codex-workflows" / "assets" / "workflows"
            / "adversarial-plugin-review" / "example-inputs.json"
        )
        code, stdout, stderr = self.invoke(
            "plan", "project:smoke-review", "--inputs", str(inputs)
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(json.loads(stdout)["planned_calls"], 16)
        code, stdout, stderr = self.invoke(
            "run", "project:smoke-review", "--inputs", str(inputs),
            "--codex-bin", str(self.fake_codex),
        )
        self.assertEqual(code, 0, stderr)
        self.assertIn("completed", stdout)


if __name__ == "__main__":
    unittest.main()
