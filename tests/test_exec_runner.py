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
    if prompt.startswith("Perform a read-only preflight inspection"):
        return {"kind": "workflow-inventory"}
    if prompt.startswith("Perform a read-only adversarial audit"):
        return {"kind": "workflow-audit-lens"}
    if prompt.startswith("Act as a skeptical evidence challenger for"):
        return {"kind": "workflow-audit-challenge"}
    if prompt.startswith("Produce the final evidence-ranked audit verdict"):
        return {"kind": "workflow-audit-verdict"}
    for prefix, kind in (
        ("BATCH-INVENTORY", "batch-inventory"),
        ("BATCH-PLAN-DESIGN", "batch-plan-design"),
        ("BATCH-DESIGN-REVIEW", "batch-design-review"),
        ("BATCH-IMPLEMENT", "batch-implement"),
        ("BATCH-ADVERSARIAL-REVIEW", "batch-adversarial-review"),
        ("BATCH-REPAIR", "batch-repair"),
        ("BATCH-FINAL-REVIEW", "batch-final-review"),
        ("BATCH-DELIVER", "batch-deliver"),
    ):
        if prompt.startswith(prefix):
            return {"kind": kind}
    for kind in (
        "terminal-missing",
        "terminal-malformed",
        "terminal-schema-invalid",
        "terminal-valid-grace",
        "terminal-descendant",
        "terminal-event-fallback",
        "terminal-event-malformed",
        "terminal-event-schema-invalid",
        "terminal-event-valid-file",
        "terminal-event-delayed-file",
        "terminal-event-conflict",
        "terminal-event-file-malformed",
        "terminal-event-file-schema-invalid",
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
    elif str(identity["kind"]).startswith("batch-"):
        scenario = os.environ.get("FAKE_BATCH_SCENARIO", "no-design")
        kind = str(identity["kind"])
        def embedded(label: str) -> dict[str, object]:
            start = prompt.index(label + "=") + len(label) + 1
            value, _ = json.JSONDecoder().raw_decode(prompt[start:])
            return value
        no_issues = scenario == "no-issues"
        preflight_blocked = scenario in {
            "active-writer",
            "dirty-base",
            "overflow",
            "unstable-reread",
        }
        design_rejected = scenario == "design-rejected"
        snapshot = "snapshot-1"
        base = "a" * 40
        tree = "1" * 64
        diff = "2" * 64
        validation = [{"name": "fixture", "status": "passed", "evidence": "fixture"}]
        finding = {
            "id": "F1",
            "severity": "high",
            "issue_numbers": [7],
            "summary": "Fixture finding",
            "evidence": "fixture",
            "required_test": "test_fixture",
        }
        def issue_traces(finding_ids: list[str] | None = None) -> list[dict[str, object]]:
            ids = finding_ids or []
            values = [{
                "number": 7,
                "content_digest": "issue-7",
                "plan_disposition": "implement",
                "depends_on": [],
                "design_required": scenario in {"design-approved", "design-rejected"},
                "design_ref": "DES-7" if scenario in {"design-approved", "design-rejected"} else "",
                "acceptance": ["works"],
                "files": ["fixture.py"],
                "tests": ["test_fixture"],
                "finding_ids": ids,
            }]
            if scenario == "overlap":
                values.append({
                    "number": 8,
                    "content_digest": "issue-8",
                    "plan_disposition": "duplicate",
                    "depends_on": [7],
                    "design_required": False,
                    "design_ref": "",
                    "acceptance": ["covered by issue 7"],
                    "files": ["fixture.py"],
                    "tests": ["test_fixture"],
                    "finding_ids": [],
                })
            return values
        if kind == "batch-inventory":
            status = "no-issues" if no_issues else ("blocked" if preflight_blocked else "ready")
            issues = []
            if status == "ready":
                issues = [{
                    "number": 7,
                    "updated_at": "2026-08-21T00:00:00Z",
                    "digest": "issue-7",
                    "title_bytes": 78 if scenario == "large-issue" else 7,
                    "body_bytes": 22602 if scenario == "large-issue" else 11,
                }]
                if scenario == "overlap":
                    issues.append({
                        "number": 8,
                        "updated_at": "2026-08-21T00:00:00Z",
                        "digest": "issue-8",
                        "title_bytes": 8,
                        "body_bytes": 12,
                    })
            aggregate_bytes = sum(item["title_bytes"] + item["body_bytes"] for item in issues)
            if scenario == "overflow":
                aggregate_bytes = 262145
            blocker = {
                "active-writer": "different nonterminal same-repository persistent writer",
                "dirty-base": "canonical checkout does not match remote base",
                "overflow": "aggregate issue evidence exceeds 256 KiB",
                "unstable-reread": "issue snapshot changed during reread",
            }.get(scenario)
            output = {
                "status": status,
                "summary": "fixture",
                "base_sha": base,
                "remote_sha": base,
                "issues": issues,
                "aggregate_bytes": aggregate_bytes,
                "issue_snapshot_digest": snapshot,
                "writer_runs": [{"run_id": "old-loop", "status": "cancelled", "evidence_digest": "terminal"}],
                "blockers": [blocker] if blocker else [],
            }
        elif kind == "batch-plan-design":
            inventory = embedded("INVENTORY")
            status = "no-issues" if inventory["status"] == "no-issues" else (
                "blocked" if inventory["status"] == "blocked" or scenario == "snapshot-mismatch" else "planned"
            )
            if scenario == "spurious-no-issues":
                status = "no-issues"
            if scenario == "mixed-state":
                status = "blocked"
            required = scenario in {"design-approved", "design-rejected"}
            items = []
            if status == "planned":
                items = [{
                    "number": 7,
                    "content_digest": "issue-7",
                    "disposition": "implement",
                    "depends_on": [],
                    "design_required": scenario in {"design-approved", "design-rejected"},
                    "design_ref": "DES-7" if scenario in {"design-approved", "design-rejected"} else "",
                    "design_required": required,
                    "design_ref": "DES-7" if required else "",
                    "acceptance": ["works"],
                    "files": ["fixture.py"],
                    "tests": ["test_fixture"],
                }]
                if scenario == "overlap":
                    items.append({
                        "number": 8,
                        "content_digest": "issue-8",
                        "disposition": "duplicate",
                        "depends_on": [7],
                        "design_required": False,
                        "design_ref": "",
                        "design_required": False,
                        "design_ref": "",
                        "acceptance": ["covered by issue 7"],
                        "files": [],
                        "tests": [],
                    })
            output = {
                "status": status,
                "summary": "fixture",
                "base_sha": base,
                "issue_snapshot_digest": snapshot,
                "items": items,
                "design_drafts": ([{
                    "page_id": "DES-7",
                    "revision": 1,
                    "status": "Review",
                    "issue_numbers": [7],
                    "evidence_digest": "design-evidence",
                }] if required and status == "planned" else []),
                "blockers": ["preflight or snapshot mismatch"] if status == "blocked" else [],
            }
        elif kind == "batch-design-review":
            inventory = embedded("INVENTORY")
            plan = embedded("PLAN")
            if "blocked" in {inventory["status"], plan["status"]}:
                status = "blocked"
            elif inventory["status"] == "no-issues" and plan["status"] == "no-issues":
                status = "no-issues"
            elif "no-issues" in {inventory["status"], plan["status"]}:
                status = "blocked"
            elif design_rejected:
                status = "rejected"
            elif scenario == "design-approved":
                status = "approved"
            else:
                status = "not-required"
            output = {
                "status": status,
                "summary": "fixture",
                "issue_snapshot_digest": snapshot,
                "issue_traces": issue_traces() if status in {"approved", "not-required", "rejected"} else [],
                "designs": ([{
                    "page_id": "DES-7",
                    "revision": 1,
                    "status": "rejected" if design_rejected else "approved",
                    "issue_numbers": [7],
                    "evidence_digest": "design-review",
                }] if scenario in {"design-approved", "design-rejected"} else []),
                "findings": ([{
                    "id": "D1",
                    "severity": "high",
                    "issue_numbers": [7],
                    "summary": "Design incomplete",
                    "evidence": "fixture",
                }] if design_rejected else []),
                "blockers": ["design rejected"] if status in {"rejected", "blocked"} else [],
            }
        elif kind == "batch-implement":
            inventory = embedded("INVENTORY")
            plan = embedded("PLAN")
            design = embedded("DESIGN_GATE")
            predecessor = {inventory["status"], plan["status"], design["status"]}
            status = "blocked" if "blocked" in predecessor or "rejected" in predecessor else (
                "no-issues" if predecessor == {"no-issues"} else (
                    "blocked" if "no-issues" in predecessor else "implemented"
                )
            )
            issues = []
            if status == "implemented":
                issues = [{
                    "number": 7,
                    "content_digest": "issue-7",
                    "plan_disposition": "implement",
                    "depends_on": [],
                    "design_required": scenario in {"design-approved", "design-rejected"},
                    "design_ref": "DES-7" if scenario in {"design-approved", "design-rejected"} else "",
                    "disposition": "implemented",
                    "acceptance": ["works"],
                    "files": ["fixture.py"],
                    "tests": ["test_fixture"],
                    "finding_ids": [],
                }]
                if scenario == "overlap":
                    issues.append({
                        "number": 8,
                        "content_digest": "issue-8",
                        "plan_disposition": "duplicate",
                        "depends_on": [7],
                        "design_required": False,
                        "design_ref": "",
                        "disposition": "duplicate",
                        "acceptance": ["covered by issue 7"],
                        "files": ["fixture.py"],
                        "tests": ["test_fixture"],
                        "finding_ids": [],
                    })
            output = {
                "status": status,
                "summary": "fixture",
                "base_sha": base,
                "issue_snapshot_digest": snapshot,
                "issues": issues,
                "changed_files": ["fixture.py"] if status == "implemented" else [],
                "validations": validation if status == "implemented" else [],
                "tree_digest": tree if status == "implemented" else "",
                "diff_digest": diff if status == "implemented" else "",
                "blockers": ["predecessor blocked"] if status == "blocked" else [],
            }
        elif kind == "batch-adversarial-review":
            implementation = embedded("IMPLEMENTATION")
            status = "no-issues" if implementation["status"] == "no-issues" else (
                "blocked" if implementation["status"] == "blocked" else (
                    "changes-required" if scenario in {"repair", "repair-blocked", "late-mixed"} else "approved"
                )
            )
            output = {
                "status": status,
                "summary": "fixture",
                "issue_snapshot_digest": snapshot,
                "tree_digest": tree if status not in {"no-issues", "blocked"} else "",
                "diff_digest": diff if status not in {"no-issues", "blocked"} else "",
                "issue_traces": issue_traces(["F1"] if status == "changes-required" else []) if status not in {"no-issues", "blocked"} else [],
                "findings": [finding] if status == "changes-required" else [],
                "validations": validation if status not in {"no-issues", "blocked"} else [],
                "blockers": ["predecessor blocked"] if status == "blocked" else [],
            }
        elif kind == "batch-repair":
            adversarial = embedded("ADVERSARIAL_REVIEW")
            status = "no-issues" if adversarial["status"] == "no-issues" else (
                "blocked" if adversarial["status"] == "blocked" or scenario in {"repair-blocked", "late-mixed"} else (
                    "repaired" if adversarial["status"] == "changes-required" else "no-repair"
                )
            )
            repaired_tree = "3" * 64 if status == "repaired" else tree
            repaired_diff = "4" * 64 if status == "repaired" else diff
            output = {
                "status": status,
                "summary": "fixture",
                "issue_snapshot_digest": snapshot,
                "issue_traces": issue_traces(["F1"] if adversarial["status"] == "changes-required" else []) if status not in {"no-issues", "blocked"} else [],
                "addressed_findings": ([{
                    "finding_id": "F1",
                    "disposition": "repaired",
                    "resolution": "fixed",
                    "test": "test_fixture",
                }] if status == "repaired" else []),
                "unresolved_findings": [],
                "changed_files": ["fixture.py"] if status == "repaired" else [],
                "validations": validation if status in {"repaired", "no-repair"} else [],
                "tree_digest": repaired_tree if status in {"repaired", "no-repair"} else "",
                "diff_digest": repaired_diff if status in {"repaired", "no-repair"} else "",
                "blockers": (["repair blocked: adversarial finding F1"] if scenario == "repair-blocked" else (["predecessor blocked"] if status == "blocked" else [])),
            }
        elif kind == "batch-final-review":
            adversarial = embedded("ADVERSARIAL_REVIEW")
            repair = embedded("REPAIR")
            if adversarial["status"] == "blocked" or repair["status"] == "blocked":
                status = "blocked"
            elif adversarial["status"] == "no-issues" and repair["status"] == "no-issues":
                status = "no-issues"
            elif "no-issues" in {adversarial["status"], repair["status"]}:
                status = "blocked"
            elif scenario == "final-rejected":
                status = "rejected"
            else:
                status = "approved"
            if scenario == "late-mixed":
                status = "approved"
            output = {
                "status": status,
                "summary": "fixture",
                "issue_snapshot_digest": snapshot,
                "reviewed_tree_digest": (
                    "9" * 64 if scenario == "digest-mismatch" else repair["tree_digest"]
                ) if status in {"approved", "rejected"} else "",
                "reviewed_diff_digest": (
                    "8" * 64 if scenario == "digest-mismatch" else repair["diff_digest"]
                ) if status in {"approved", "rejected"} else "",
                "reviewed_paths": ([{
                    "path": "fixture.py",
                    "mode": "100644",
                    "change": "modified",
                    "sha256": "5" * 64,
                }] if status in {"approved", "rejected"} else []),
                "issue_traces": issue_traces(["F1"] if scenario in {"repair", "repair-blocked"} else []) if status in {"approved", "rejected"} else [],
                "finding_dispositions": ([{
                    "finding_id": "F1",
                    "status": "resolved",
                    "resolution": "fixed",
                    "test": "test_fixture",
                }] if scenario == "repair" and status == "approved" else []),
                "findings": ([{
                    "id": "F2",
                    "severity": "high",
                    "issue_numbers": [7],
                    "summary": "Final rejection",
                    "evidence": "fixture",
                }] if status == "rejected" else []),
                "validations": validation if status in {"approved", "rejected"} else [],
                "blockers": (
                    ["final-review rejected: F2"] if status == "rejected" else (
                        ["final-review blocked by repair finding F1"] if status == "blocked" and scenario == "repair-blocked" else (
                            ["final gate"] if status == "blocked" else []
                        )
                    )
                ),
            }
        else:
            inventory = embedded("INVENTORY")
            plan = embedded("PLAN")
            design = embedded("DESIGN_GATE")
            implementation = embedded("IMPLEMENTATION")
            adversarial = embedded("ADVERSARIAL_REVIEW")
            repair = embedded("REPAIR")
            final_review = embedded("FINAL_REVIEW")
            statuses = {
                inventory["status"], plan["status"], design["status"],
                implementation["status"], adversarial["status"], repair["status"],
                final_review["status"],
            }
            upstream_blocked = bool(statuses & {"blocked", "rejected"})
            digest_mismatch = final_review["status"] == "approved" and (
                final_review["reviewed_tree_digest"] != repair["tree_digest"]
                or final_review["reviewed_diff_digest"] != repair["diff_digest"]
            )
            if upstream_blocked or digest_mismatch:
                status = "blocked"
            elif statuses == {"no-issues"}:
                status = "no-issues"
            elif "no-issues" in statuses or final_review["status"] != "approved":
                status = "blocked"
            elif scenario == "prepared-write-failure":
                status = "blocked"
            elif scenario == "pending-reconciliation":
                status = "published-pending-reconciliation"
            else:
                status = "delivered"
            recovery_only = scenario == "recovery-only"
            if status == "delivered":
                actions = (
                    [
                        {"action": "recover-reconciliation", "status": "completed", "evidence": "fixture"},
                        {"action": "verify-remote", "status": "completed", "evidence": "fixture"},
                        {"action": "reinstall-plugin", "status": "completed", "evidence": "fixture"},
                        {"action": "close-issues", "status": "completed", "evidence": "fixture"},
                        {"action": "sync-repository-authority", "status": "completed", "evidence": "fixture"},
                        {"action": "sync-delivery-task", "status": "completed", "evidence": "fixture"},
                        {"action": "sync-feature-map", "status": "completed", "evidence": "fixture"},
                    ] if recovery_only else [
                        {"action": "commit", "status": "completed", "evidence": "fixture"},
                        {"action": "persist-prepared", "status": "completed", "evidence": "fixture"},
                        {"action": "push", "status": "completed", "evidence": "fixture"},
                        {"action": "persist-published-pending", "status": "completed", "evidence": "fixture"},
                        {"action": "verify-remote", "status": "completed", "evidence": "fixture"},
                        {"action": "reinstall-plugin", "status": "completed", "evidence": "fixture"},
                        {"action": "close-issues", "status": "completed", "evidence": "fixture"},
                        {"action": "sync-repository-authority", "status": "completed", "evidence": "fixture"},
                        {"action": "sync-delivery-task", "status": "completed", "evidence": "fixture"},
                        {"action": "sync-feature-map", "status": "completed", "evidence": "fixture"},
                    ]
                )
            elif status == "published-pending-reconciliation":
                actions = [
                    {"action": "commit", "status": "completed", "evidence": "fixture"},
                    {"action": "persist-prepared", "status": "completed", "evidence": "fixture"},
                    {"action": "push", "status": "completed", "evidence": "fixture"},
                    {"action": "persist-published-pending", "status": "completed", "evidence": "fixture"},
                ]
            elif scenario == "prepared-write-failure":
                actions = [
                    {"action": "commit", "status": "completed", "evidence": "fixture"},
                    {"action": "persist-prepared", "status": "failed", "evidence": "fixture"},
                    {"action": "push", "status": "not-run", "evidence": "fixture"},
                ]
            else:
                actions = []
            output = {
                "status": status,
                "summary": "fixture",
                "base_sha": base,
                "reviewed_tree_digest": final_review["reviewed_tree_digest"] if final_review["status"] in {"approved", "rejected"} else "",
                "reviewed_diff_digest": final_review["reviewed_diff_digest"] if final_review["status"] in {"approved", "rejected"} else "",
                "issue_snapshot_digest": snapshot,
                "delivered_sha": "b" * 40 if status in {"delivered", "published-pending-reconciliation"} else "",
                "recovery_schema_version": "github-issues-batch-recovery.v1",
                "recovery_phase": (
                    "reconciled" if status == "delivered" else (
                        "published-pending-reconciliation" if status == "published-pending-reconciliation" else "none"
                    )
                ),
                "issues": ([
                    {
                        **trace,
                        "status": "closed" if status == "delivered" and trace["plan_disposition"] == "implement" else "unchanged",
                        "evidence": "fixture",
                    }
                    for trace in final_review["issue_traces"]
                ] if status in {"delivered", "published-pending-reconciliation"} else []),
                "recovery_manifest_digest": "manifest" if status in {"delivered", "published-pending-reconciliation"} else "",
                "actions": actions,
                "validations": validation if status in {"delivered", "published-pending-reconciliation"} else [],
                "reconciliation": {
                    "remote": "completed" if status == "delivered" else ("pending" if status == "published-pending-reconciliation" else "not-started"),
                    "plugin": "completed" if status == "delivered" else "not-started",
                    "issues": "completed" if status == "delivered" else "not-started",
                    "repository_authority": "completed" if status == "delivered" else "not-started",
                    "delivery_task": "completed" if status == "delivered" else "not-started",
                    "feature_map": "completed" if status == "delivered" else "not-started",
                    "evidence": ["fixture"] if status in {"delivered", "published-pending-reconciliation"} else [],
                },
                "blockers": (
                    [(
                        "deliver blocked by digest mismatch"
                        if digest_mismatch else
                        (
                            "deliver blocked by prepared manifest persistence"
                            if scenario == "prepared-write-failure" else
                            (
                                f"deliver blocked by repair {repair['status']}: " + ",".join(repair["blockers"])
                                if repair["status"] == "blocked" else
                                f"deliver blocked by final-review {final_review['status']}: " + ",".join(final_review["blockers"])
                            )
                        )
                    )]
                    if status == "blocked" else []
                ),
            }
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
    elif identity["kind"] == "workflow-inventory":
        output = {
            "status": "success",
            "target_workflow": "builtin:fanout-synthesize",
            "resolved_scope": "builtin",
            "workflow_id": "fanout-synthesize",
            "schema_version": "codex-exec-workflow.v1",
            "validation_status": "valid",
            "plan_status": "valid",
            "workflow_digest": "fixture-digest",
            "loop_mode": "finite",
            "task_order": ["analyze-items", "synthesize"],
            "task_count": 2,
            "planned_calls": "6",
            "resolved_agents": [],
            "evidence": ["fixture:workflow.json"],
            "errors": [],
            "unresolved": [],
        }
    elif identity["kind"] == "workflow-audit-lens":
        output = {
            "status": "success",
            "lens": "fixture-lens",
            "summary": "No fixture defect",
            "findings": [],
            "tested_assumptions": ["Target plan is bounded"],
            "no_issue_areas": ["Fixture lens"],
            "unresolved": [],
        }
    elif identity["kind"] == "workflow-audit-challenge":
        output = {
            "status": "success",
            "summary": "No candidate findings",
            "inventory_assessment": "Preflight evidence is consistent",
            "verdicts": [],
            "cross_cutting_gaps": [],
            "unresolved": [],
            "reviewed_findings": 0,
        }
    elif identity["kind"] == "workflow-audit-verdict":
        output = {
            "status": "success",
            "target_workflow": "builtin:fanout-synthesize",
            "workflow_digest": "fixture-digest",
            "validation_status": "valid",
            "plan_status": "valid",
            "audit_recommendation": "approve",
            "summary": "Fixture workflow audit passed",
            "confirmed_findings": [],
            "rejected_or_duplicate_findings": [],
            "coverage_gaps": [],
            "required_actions": [],
            "optional_improvements": [],
            "reviewed_lenses": 3,
            "reviewed_findings": 0,
        }
    elif identity["kind"] == "healthy-events":
        for index in range(6):
            print(json.dumps({"type": "item.updated", "index": index}), flush=True)
            time.sleep(0.05)
        output = {"ok": True}
    elif str(identity["kind"]).startswith("terminal-event-"):
        final_path = Path(option("--output-last-message"))
        final_path.parent.mkdir(parents=True, exist_ok=True)
        event_kind = identity["kind"]
        if event_kind == "terminal-event-malformed":
            event_text = "{invalid"
        elif event_kind == "terminal-event-schema-invalid":
            event_text = json.dumps({"ok": "not-a-boolean"})
        else:
            event_text = json.dumps({"ok": True})
        if event_kind == "terminal-event-fallback":
            print(json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps({"ok": False})},
            }), flush=True)
            print(json.dumps({
                "type": "item.completed",
                "item": {"type": "message", "text": json.dumps({"ok": False})},
            }), flush=True)
        print(json.dumps({
            "type": "item.completed",
            "item": {"type": "agent_message", "text": event_text},
        }), flush=True)
        print(json.dumps({"type": "turn.completed"}), flush=True)
        if event_kind == "terminal-event-valid-file":
            time.sleep(0.1)
            final_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        elif event_kind == "terminal-event-delayed-file":
            time.sleep(0.3)
            final_path.write_text(json.dumps({"ok": True}), encoding="utf-8")
        elif event_kind == "terminal-event-conflict":
            time.sleep(0.1)
            final_path.write_text(json.dumps({"ok": False}), encoding="utf-8")
        elif event_kind == "terminal-event-file-malformed":
            time.sleep(0.1)
            final_path.write_text("{invalid", encoding="utf-8")
        elif event_kind == "terminal-event-file-schema-invalid":
            time.sleep(0.1)
            final_path.write_text(json.dumps({"ok": "not-a-boolean"}), encoding="utf-8")
        time.sleep(60)
        raise SystemExit(0)
    elif str(identity["kind"]).startswith("terminal-"):
        final_path = Path(option("--output-last-message"))
        final_path.parent.mkdir(parents=True, exist_ok=True)
        if identity["kind"] == "terminal-valid-grace":
            print(json.dumps({
                "type": "item.completed",
                "item": {"type": "agent_message", "text": json.dumps({"ok": True})},
            }), flush=True)
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
    def test_input_templated_execution_settings_resolve_and_are_persisted(self) -> None:
        workflow = self.write_workflow(
            "input-profile",
            [{
                "id": "worker",
                "depends_on": [],
                "prompt": "PROFILE",
                "output_schema": "schemas/worker.json",
                "model": "{{ inputs.worker-model }}",
                "model_allowlist": ["gpt-5.6-luna"],
                "reasoning_effort": "{{ inputs.worker-effort }}",
                "reasoning_effort_allowlist": ["medium", "high"],
            }],
            {"worker": OBJECT_SCHEMA},
            inputs={"worker-model": "string", "worker-effort": "string"},
        )
        values = [
            "--input", 'worker-model="gpt-5.6-luna"',
            "--input", 'worker-effort="medium"',
        ]
        code, stdout, stderr = self.invoke("plan", str(workflow), *values)
        self.assertEqual(code, 0, stderr)
        task = json.loads(stdout)["tasks"][0]
        self.assertEqual((task["model"], task["reasoning_effort"]), ("gpt-5.6-luna", "medium"))

        code, _, stderr = self.invoke(
            "run", str(workflow), *values, "--codex-bin", str(self.fake_codex)
        )
        self.assertEqual(code, 0, stderr)
        state = json.loads((self.only_run_dir() / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["plan"][0]["model"], "gpt-5.6-luna")
        call = self.fake_log()["calls"][0]
        self.assertIn("gpt-5.6-luna", call["argv"])
        self.assertIn('model_reasoning_effort="medium"', call["argv"])

    def test_input_templated_execution_settings_fail_closed(self) -> None:
        base = {
            "id": "worker",
            "depends_on": [],
            "prompt": "PROFILE",
            "output_schema": "schemas/worker.json",
            "model": "{{ inputs.worker-model }}",
            "model_allowlist": ["gpt-5.6-luna"],
        }
        for name, task, inputs, error in (
            ("profile-unknown", base, {"other": "string"}, "declared string input"),
            ("profile-no-allowlist", {k: v for k, v in base.items() if k != "model_allowlist"}, {"worker-model": "string"}, "allowlist"),
            ("profile-task-output", {**base, "model": "{{ tasks.worker.output }}"}, {"worker-model": "string"}, "declared string input"),
            ("profile-embedded-input", {**base, "model": "prefix-{{ inputs.worker-model }}"}, {"worker-model": "string"}, "exactly one"),
            ("profile-embedded-task", {**base, "model": "{{ tasks.worker.output }}-suffix"}, {"worker-model": "string"}, "exactly one"),
            ("profile-embedded-item", {**base, "model": "{{ item }}-suffix"}, {"worker-model": "string"}, "exactly one"),
        ):
            with self.subTest(name=name):
                workflow = self.write_workflow(name, [task], {"worker": OBJECT_SCHEMA}, inputs=inputs)
                with self.assertRaisesRegex(ContractError, error):
                    load_workflow(workflow / "workflow.json", self.project)

        workflow = self.write_workflow(
            "profile-denied",
            [base],
            {"worker": OBJECT_SCHEMA},
            inputs={"worker-model": "string"},
        )
        code, _, stderr = self.invoke(
            "plan", str(workflow), "--input", 'worker-model="gpt-5.6-sol"'
        )
        self.assertEqual(code, 2)
        self.assertIn("is not allowed", stderr)

    def test_finite_run_rejects_persisted_input_or_plan_tampering(self) -> None:
        workflow = self.write_workflow(
            "profile-tamper",
            [{
                "id": "worker",
                "depends_on": [],
                "prompt": "PROFILE {{ inputs.request }}",
                "output_schema": "schemas/worker.json",
                "model": "{{ inputs.worker-model }}",
                "model_allowlist": ["gpt-5.6-luna"],
            }],
            {"worker": OBJECT_SCHEMA},
            inputs={"worker-model": "string", "request": "string"},
        )
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run", str(workflow), "--input", 'worker-model="gpt-5.6-luna"',
                "--input", 'request="original"',
                "--codex-bin", str(self.fake_codex), "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        state_path = run_dir / "run.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        self.assertIn("input_digest", state)
        state.pop("input_digest")
        state["inputs"]["request"] = "tampered"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        worker_code, _, worker_stderr = self.invoke("_worker", str(run_dir))
        self.assertEqual(worker_code, 2)
        self.assertIn("required by this run schema", worker_stderr)

    def test_finite_run_rejects_resolved_plan_tampering(self) -> None:
        workflow = self.write_workflow(
            "profile-plan-tamper",
            [{
                "id": "worker", "depends_on": [], "prompt": "PROFILE",
                "output_schema": "schemas/worker.json",
                "model": "{{ inputs.worker-model }}",
                "model_allowlist": ["gpt-5.6-luna"],
            }],
            {"worker": OBJECT_SCHEMA},
            inputs={"worker-model": "string"},
        )
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run", str(workflow), "--input", 'worker-model="gpt-5.6-luna"',
                "--codex-bin", str(self.fake_codex), "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        state_path = run_dir / "run.json"
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["plan"][0]["model"] = "gpt-5.6-sol"
        state_path.write_text(json.dumps(state), encoding="utf-8")
        worker_code, _, worker_stderr = self.invoke("_worker", str(run_dir))
        self.assertEqual(worker_code, 2)
        self.assertIn("resolved execution settings no longer match", worker_stderr)

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
            "event-malformed": (
                "TERMINAL-EVENT-MALFORMED",
                "terminal_event_output_malformed",
                "malformed",
            ),
            "event-schema": (
                "TERMINAL-EVENT-SCHEMA-INVALID",
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
        self.assertEqual(state["tasks"]["grace"]["reconciliation_source"], "output_file")
        self.assertEqual(state["tasks"]["grace"]["output_validation_state"], "valid")
        self.assertIsNotNone(state["tasks"]["grace"]["output_valid_at"])
        self.assertIsNotNone(state["tasks"]["grace"]["process_exit_at"])
        self.assertIsNone(state["tasks"]["healthy"]["terminal_event_at"])
        self.assertEqual(state["tasks"]["healthy"]["output_validation_state"], "valid")
        self.assertEqual(state["tasks"]["healthy"]["reconciliation_source"], "output_file")

    def test_terminal_event_agent_message_fallback_survives_delayed_output_file(self) -> None:
        workflow = self.write_workflow(
            "terminal-event-fallback",
            [
                {
                    "id": "fallback",
                    "depends_on": [],
                    "prompt": "TERMINAL-EVENT-FALLBACK",
                    "output_schema": "schemas/out.json",
                },
                {
                    "id": "delayed",
                    "depends_on": [],
                    "prompt": "TERMINAL-EVENT-DELAYED-FILE",
                    "output_schema": "schemas/out.json",
                },
            ],
            {"out": OBJECT_SCHEMA},
        )
        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.1"}):
            code, _, stderr = self.invoke(
                "run", str(workflow), "--codex-bin", str(self.fake_codex)
            )

        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "completed")
        for task_id in ("fallback", "delayed"):
            with self.subTest(task=task_id):
                task = state["tasks"][task_id]
                self.assertEqual(task["reconciliation_source"], "event_agent_message")
                self.assertEqual(task["reconciliation_reason"], "terminal_event_with_valid_output")
                self.assertEqual(
                    json.loads((run_dir / "tasks" / task_id / "final.json").read_text()),
                    {"ok": True},
                )
                attempt = run_dir / "tasks" / task_id / "attempt-1"
                self.assertTrue((attempt / ".event-fallback.json").is_file())

    def test_terminal_event_file_remains_authoritative_when_valid(self) -> None:
        workflow = self.write_workflow(
            "terminal-event-file",
            [{
                "id": "work",
                "depends_on": [],
                "prompt": "TERMINAL-EVENT-VALID-FILE",
                "output_schema": "schemas/out.json",
            }],
            {"out": OBJECT_SCHEMA},
        )
        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.4"}):
            code, _, stderr = self.invoke(
                "run", str(workflow), "--codex-bin", str(self.fake_codex)
            )

        self.assertEqual(code, 0, stderr)
        state = json.loads((self.only_run_dir() / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["tasks"]["work"]["reconciliation_source"], "output_file")

    def test_event_parser_uses_last_agent_message_before_terminal(self) -> None:
        from workflow_governor import _exec_runner_impl as implementation

        events_path = self.root / "events.jsonl"
        events_path.write_text(
            "\n".join(
                [
                    json.dumps({
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": json.dumps({"ok": False})},
                    }),
                    json.dumps({
                        "type": "item.completed",
                        "item": {"type": "message", "text": json.dumps({"ok": False})},
                    }),
                    json.dumps({
                        "type": "item.completed",
                        "item": {"type": "agent_message", "text": json.dumps({"ok": True})},
                    }),
                    json.dumps({"type": "turn.completed"}),
                    '{"type":"item.completed"',
                ]
            ),
            encoding="utf-8",
        )
        state, result, error = implementation._event_output_state(events_path, OBJECT_SCHEMA)
        self.assertEqual(state, "valid")
        self.assertEqual(result, {"ok": True})
        self.assertIsNone(error)

    def test_terminal_event_conflicting_valid_payloads_fail_closed(self) -> None:
        workflow = self.write_workflow(
            "terminal-event-conflict",
            [{
                "id": "work",
                "depends_on": [],
                "prompt": "TERMINAL-EVENT-CONFLICT",
                "output_schema": "schemas/out.json",
            }],
            {"out": OBJECT_SCHEMA},
        )
        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.4"}):
            code, _, stderr = self.invoke(
                "run", str(workflow), "--codex-bin", str(self.fake_codex)
            )

        self.assertEqual(code, 1, stderr)
        state = json.loads((self.only_run_dir() / "run.json").read_text(encoding="utf-8"))
        task = state["tasks"]["work"]
        self.assertEqual(task["failure_reason"], "terminal_event_output_conflict")
        self.assertEqual(task["reconciliation_reason"], "terminal_event_output_conflict")
        self.assertEqual(task["output_validation_state"], "conflict")
        self.assertIsNone(task["reconciliation_source"])
        self.assertEqual([item["kind"] for item in self.fake_log()["starts"]], ["terminal-event-conflict"])

    def test_invalid_declared_file_is_not_replaced_by_valid_event(self) -> None:
        modes = {
            "malformed": (
                "TERMINAL-EVENT-FILE-MALFORMED",
                "terminal_event_output_malformed",
                "malformed",
            ),
            "schema": (
                "TERMINAL-EVENT-FILE-SCHEMA-INVALID",
                "terminal_event_output_schema_invalid",
                "schema-invalid",
            ),
        }
        workflow = self.write_workflow(
            "terminal-event-invalid-file",
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
        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.4"}):
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
                self.assertIsNone(task["reconciliation_source"])

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

    def test_supervisor_restart_uses_event_fallback_without_retry(self) -> None:
        workflow = self.write_workflow(
            "restart-event-fallback",
            [{
                "id": "work",
                "depends_on": [],
                "prompt": "TERMINAL-EVENT-FALLBACK",
                "output_schema": "schemas/out.json",
                "retries": 1,
            }],
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
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=5)

        with patch.dict(os.environ, {"CODEX_WORKFLOWS_TERMINAL_GRACE_SECONDS": "0.4"}):
            code, _, stderr = self.invoke("_worker", str(run_dir))

        self.assertEqual(code, 0, stderr)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["tasks"]["work"]["attempt"], 1)
        self.assertEqual(state["tasks"]["work"]["reconciliation_source"], "event_agent_message")
        self.assertEqual([item["kind"] for item in self.fake_log()["starts"]], ["terminal-event-fallback"])


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
        self.assertTrue(all(task["reasoning_effort"] == "medium" for task in plan["tasks"]))

    def test_builtin_workflow_audit_installs_plans_and_runs_read_only(self) -> None:
        install_code, _, install_stderr = self.invoke(
            "workflow", "install", "builtin:workflow-audit", "--name", "workflow-audit"
        )
        self.assertEqual(install_code, 0, install_stderr)
        code, stdout, stderr = self.invoke(
            "workflow", "validate", "project:workflow-audit"
        )
        self.assertEqual(code, 0, stderr)
        self.assertEqual(
            json.loads(stdout)["task_order"],
            ["inspect-workflow", "audit-lenses", "challenge-findings", "audit-verdict"],
        )

        audit_inputs = (
            Path(__file__).resolve().parents[1]
            / "skills/codex-workflows/assets/workflows/workflow-audit/example-inputs.json"
        )
        plan_code, plan_stdout, plan_stderr = self.invoke(
            "plan", "project:workflow-audit", "--inputs", str(audit_inputs)
        )
        self.assertEqual(plan_code, 0, plan_stderr)
        plan = json.loads(plan_stdout)
        self.assertEqual(plan["planned_calls"], 18)
        self.assertEqual(plan["max_parallel"], 6)
        self.assertEqual(plan["tasks"][1]["fanout_items"], 6)
        self.assertTrue(all(task["sandbox"] == "read-only" for task in plan["tasks"]))
        self.assertTrue(all(task["model"] == "gpt-5.6-sol" for task in plan["tasks"]))
        self.assertTrue(all(task["reasoning_effort"] == "medium" for task in plan["tasks"]))
        self.assertEqual(plan["tasks"][0]["agent"], "workflow-auditor")
        self.assertEqual(plan["tasks"][-1]["agent"], "workflow-audit-judge")

        run_code, run_stdout, run_stderr = self.invoke(
            "run", "project:workflow-audit", "--inputs", str(audit_inputs),
            "--codex-bin", str(self.fake_codex),
        )
        self.assertEqual(run_code, 0, run_stderr)
        self.assertIn("completed", run_stdout)
        result_code, result_stdout, result_stderr = self.invoke(
            "result", self.only_run_dir().name
        )
        self.assertEqual(result_code, 0, result_stderr)
        verdict = json.loads(result_stdout)["audit-verdict"]
        self.assertEqual(verdict["audit_recommendation"], "approve")

    def test_non_review_templates_pin_luna_high(self) -> None:
        builtin_root = (
            Path(__file__).resolve().parents[1]
            / "skills/codex-workflows/assets/workflows"
        )
        for name in ("fanout-synthesize", "github-issue-worker", "loop-monitor"):
            with self.subTest(name=name):
                workflow = load_workflow(builtin_root / name / "workflow.json")
                self.assertTrue(
                    all(task["model"] == "gpt-5.6-luna" for task in workflow["tasks"].values())
                )
                self.assertTrue(
                    all(task["reasoning_effort"] == "high" for task in workflow["tasks"].values())
                )

        init_code, _, init_stderr = self.invoke(
            "workflow", "init", "starter-profile", "--scope", "project"
        )
        self.assertEqual(init_code, 0, init_stderr)
        starter = load_workflow(
            self.project / ".codex/exec-workflows/starter-profile/workflow.json"
        )
        self.assertTrue(
            all(task["model"] == "gpt-5.6-luna" for task in starter["tasks"].values())
        )
        self.assertTrue(
            all(task["reasoning_effort"] == "high" for task in starter["tasks"].values())
        )

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

    def outcome_workflow(self, name: str = "outcome-contract") -> Path:
        discovery = {
            "type": "object",
            "properties": {
                "selected_cursor": {"type": "string"},
                "selected": {"type": "boolean"},
            },
            "required": ["selected_cursor", "selected"],
            "additionalProperties": False,
        }
        delivery = {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": ["delivered", "no-issue", "blocked"]},
                "committed_cursor": {"type": "string"},
            },
            "required": ["status", "committed_cursor"],
            "additionalProperties": False,
        }
        review = {
            "type": "object",
            "properties": {"verdict": {"type": "string"}, "findings": {"type": "array", "items": {"type": "string"}}},
            "required": ["verdict", "findings"],
            "additionalProperties": False,
        }
        loop = self.loop_config(max_failures=2)
        loop.update(
            {
                "cursor": "tasks.deliver.output.committed_cursor",
                "cursor_input": "cursor",
                "outcome": {
                    "path": "tasks.deliver.output.status",
                    "success_values": ["delivered", "no-issue"],
                    "failure_values": ["blocked"],
                    "failure_key": "tasks.discover.output.selected_cursor",
                    "feedback_path": "tasks.review.output",
                    "feedback_input": "repair-context",
                },
            }
        )
        tasks = [
            {"id": "discover", "depends_on": [], "prompt": "DISCOVER {{ inputs.cursor }}", "output_schema": "schemas/discovery.json"},
            {"id": "review", "depends_on": ["discover"], "prompt": "REVIEW {{ tasks.discover.output }}", "output_schema": "schemas/review.json"},
            {"id": "implement", "depends_on": ["discover"], "prompt": "IMPLEMENT {{ inputs.repair-context }}", "output_schema": "schemas/review.json"},
            {"id": "deliver", "depends_on": ["discover", "review", "implement"], "prompt": "DELIVER", "output_schema": "schemas/delivery.json"},
        ]
        return self.write_workflow(
            name,
            tasks,
            {"discovery": discovery, "delivery": delivery, "review": review},
            inputs={"cursor": "string", "source": "string", "repair-context": "object"},
            max_parallel=1,
            loop=loop,
        )

    def test_outcome_contract_and_legacy_loop_compatibility(self) -> None:
        workflow = self.outcome_workflow()
        loaded = load_workflow(workflow / "workflow.json", self.project)
        self.assertEqual(loaded["loop"]["outcome"]["failure_values"], ["blocked"])
        raw = json.loads((workflow / "workflow.json").read_text(encoding="utf-8"))
        raw["loop"]["outcome"]["success_values"] = ["delivered", "blocked"]
        (workflow / "workflow.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "must not overlap"):
            load_workflow(workflow / "workflow.json", self.project)
        raw["loop"]["outcome"]["success_values"] = ["delivered"]
        (workflow / "workflow.json").write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(ContractError, "completely cover"):
            load_workflow(workflow / "workflow.json", self.project)
        raw["loop"].pop("outcome")
        (workflow / "workflow.json").write_text(json.dumps(raw), encoding="utf-8")
        legacy = load_workflow(workflow / "workflow.json", self.project)
        self.assertIsNone(legacy["loop"]["outcome"])

    def test_semantic_failure_preserves_checkpoint_and_opens_keyed_circuit(self) -> None:
        workflow = self.outcome_workflow("outcome-run")
        with patch("workflow_governor._exec_runner_impl.subprocess.Popen"):
            code, _, stderr = self.invoke(
                "run",
                str(workflow),
                "--input", 'cursor="old"',
                "--input", 'source="fixture"',
                "--input", 'repair-context={}',
                "--codex-bin", str(self.fake_codex),
                "--detach",
            )
        self.assertEqual(code, 0, stderr)
        run_dir = self.only_run_dir()
        implementation = sys.modules["workflow_governor._exec_runner_impl"]

        async def fake_cycle(cycle_dir: Path) -> int:
            state = json.loads((cycle_dir / "run.json").read_text(encoding="utf-8"))
            outputs = {
                "discover": {"selected_cursor": "issue-6", "selected": True},
                "review": {"verdict": "changes-required", "findings": ["finding"]},
                "implement": {"verdict": "approved", "findings": []},
                "deliver": {"status": "blocked", "committed_cursor": "old"},
            }
            for task_id, output in outputs.items():
                task_dir = cycle_dir / "tasks" / task_id
                task_dir.mkdir(parents=True, exist_ok=True)
                implementation._atomic_json(task_dir / "final.json", output)
                state["tasks"][task_id] = {"status": "completed"}
            state["status"] = "completed"
            implementation._atomic_json(cycle_dir / "run.json", state)
            return 0

        async def keep_running(_target: Path, _wake_at: object) -> str:
            return "running"

        with patch("workflow_governor._exec_runner_impl._execute_run", new=fake_cycle), patch(
            "workflow_governor._exec_runner_impl._wait_for_loop_wake", new=keep_running
        ):
            self.assertEqual(asyncio.run(execute_run(run_dir)), 1)
        state = json.loads((run_dir / "run.json").read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "circuit-open")
        self.assertEqual(state["checkpoint"]["cycle_id"], 0)
        self.assertEqual(state["consecutive_failures"], 2)
        self.assertEqual(state["failure_key_digest"], implementation.digest_json("issue-6"))
        feedback = json.loads((run_dir / "feedback.json").read_text(encoding="utf-8"))
        self.assertEqual(feedback["key"], "issue-6")
        lifecycle = (run_dir / "STATE.md").read_text(encoding="utf-8")
        self.assertNotIn("finding", lifecycle)

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

    def test_github_delivery_discovery_can_use_detached_networked_worktree(self) -> None:
        repository = Path(__file__).resolve().parent.parent
        workflow = load_workflow(
            repository
            / ".codex/exec-workflows/github-issue-delivery/workflow.json",
            repository,
        )

        discover = workflow["tasks"]["discover"]
        implement = workflow["tasks"]["implement"]
        self.assertEqual(workflow["loop"]["interval_seconds"], 5)
        self.assertEqual(workflow["loop"]["jitter_seconds"], 0)
        self.assertEqual(discover["sandbox"], "danger-full-access")
        self.assertEqual(discover["write_isolation"], "git-worktree")
        self.assertIn("detached worktree", discover["prompt"])
        self.assertIn("local branch checkout is unnecessary", discover["prompt"])
        prompts = [task["prompt"] for task in workflow["tasks"].values()]
        self.assertLessEqual(sum(len(prompt.encode("utf-8")) for prompt in prompts), 5000)
        prompt_contract = {
            "discover": ("untrusted", "minimum `(updatedAt, number)`", "unchanged OPEN", "previous cursor", "Never edit"),
            "implement": ("untrusted", "matching prior feedback", "HEAD=base_sha", "smallest complete fix", "focused tests", "Never stage"),
            "review": ("Read-only", "actual diff", "zero actionable findings", "Never edit"),
            "repair": ("same worktree", "repair every confirmed finding", "regression test", "no-op/no-repair", "Never commit"),
            "final-review": ("Independent read-only final gate", "actual complete diff", "every finding", "concrete changes-required", "Never modify"),
            "deliver": ("independently approved", "stop at first failure", "exact remote ref equal base_sha", "non-force push exactly", "verify selector/version", "close only selected issue", "previous cursor", "zero-padded-20-digit-number", "Never force-push"),
        }
        for task_id, required in prompt_contract.items():
            with self.subTest(task_id=task_id):
                prompt = workflow["tasks"][task_id]["prompt"]
                for marker in required:
                    self.assertIn(marker, prompt)
        self.assertEqual(workflow["loop"]["max_consecutive_failures"], 2)
        self.assertEqual(workflow["loop"]["outcome"]["feedback_input"], "repair-context")
        self.assertEqual(implement["model"], "{{ inputs.coding-model }}")
        self.assertEqual(implement["reasoning_effort"], "{{ inputs.coding-effort }}")
        self.assertEqual(implement["reasoning_effort_allowlist"], ["high"])

        inputs = json.loads(
            (repository / ".codex/exec-workflows/github-issue-delivery/example-inputs.json")
            .read_text(encoding="utf-8")
        )
        code, stdout, stderr = self.invoke(
            "plan",
            str(repository / ".codex/exec-workflows/github-issue-delivery"),
            "--inputs", str(repository / ".codex/exec-workflows/github-issue-delivery/example-inputs.json"),
            "--max-calls", "6",
        )
        self.assertEqual(code, 0, stderr)
        plan = json.loads(stdout)
        effective = {task["id"]: (task["model"], task["reasoning_effort"]) for task in plan["tasks"]}
        self.assertEqual(effective["discover"], (inputs["routine-model"], inputs["routine-effort"]))
        self.assertEqual(effective["review"], (inputs["routine-model"], inputs["routine-effort"]))
        for task_id in ("implement", "repair", "deliver"):
            self.assertEqual(effective[task_id], (inputs["coding-model"], inputs["coding-effort"]))
        self.assertEqual(effective["final-review"], (inputs["gate-model"], inputs["gate-effort"]))

    def test_github_issues_batch_is_finite_bounded_and_review_gated(self) -> None:
        repository = Path(__file__).resolve().parent.parent
        directory = repository / ".codex/exec-workflows/github-issues-batch"
        workflow = load_workflow(directory / "workflow.json", repository)
        self.assertIsNone(workflow["loop"])
        self.assertEqual(workflow["max_parallel"], 1)
        self.assertEqual(
            workflow["order"],
            ["inventory", "plan-design", "design-review", "implement", "adversarial-review", "repair", "final-review", "deliver"],
        )
        prompts = {task_id: task["prompt"] for task_id, task in workflow["tasks"].items()}
        for marker in (
            "absolute limit is 100 issues",
            "aggregate complete issue evidence is at most 256 KiB",
            "There is no per-issue byte ceiling",
            "different nonterminal run",
            "non-null root loop",
            "do not edit files",
        ):
            self.assertIn(marker, prompts["inventory"])
        self.assertNotIn("16 KiB", "\n".join(prompts.values()))
        for task_id in ("inventory", "implement", "repair", "deliver"):
            for marker in ("different nonterminal", "finite run", "terminal", "other repo"):
                self.assertIn(marker, prompts[task_id])
        inputs = json.loads((directory / "example-inputs.json").read_text(encoding="utf-8"))
        self.assertNotIn("max-issues", workflow["inputs"])
        self.assertNotIn("max-issues", inputs)
        self.assertIn("draft content and review evidence", prompts["plan-design"])
        self.assertIn("zero actionable finding", prompts["design-review"])
        self.assertEqual(workflow["tasks"]["design-review"]["sandbox"], "read-only")
        self.assertIn("one pass implement", prompts["implement"])
        self.assertIn("Adversarial read-only review", prompts["adversarial-review"])
        self.assertIn("single plugin cachebuster update", prompts["repair"])
        self.assertIn("exact complete publishable diff", prompts["final-review"])
        for task_id in workflow["order"][:-1]:
            self.assertIn(f"{{{{ tasks.{task_id}.output }}}}", prompts["deliver"])
        for marker in ("final-review.status is exactly approved", "Never modify tracked files", "atomic rename", "mode 0600", "published-pending-reconciliation", "never rebuilds or repushes"):
            self.assertIn(marker, prompts["deliver"])
        schemas = {task["output_schema"] for task in workflow["tasks"].values()}
        self.assertEqual(len(schemas), 8)

        code, stdout, stderr = self.invoke(
            "plan", str(directory), "--inputs", str(directory / "example-inputs.json"), "--max-calls", "8"
        )
        self.assertEqual(code, 0, stderr)
        plan = json.loads(stdout)
        self.assertEqual(plan["planned_calls"], 8)
        self.assertEqual(plan["tasks"][2]["model"], "gpt-5.6-sol")
        self.assertEqual(plan["tasks"][4]["reasoning_effort"], "high")

    def test_github_issues_batch_fake_codex_status_paths(self) -> None:
        repository = Path(__file__).resolve().parent.parent
        directory = repository / ".codex/exec-workflows/github-issues-batch"
        scenarios = {
            "no-issues": ("no-issues", "no-issues", "no-issues", "no-issues", "no-issues"),
            "no-design": ("not-required", "approved", "no-repair", "approved", "delivered"),
            "design-approved": ("approved", "approved", "no-repair", "approved", "delivered"),
            "design-rejected": ("rejected", "blocked", "blocked", "blocked", "blocked"),
            "overlap": ("not-required", "approved", "no-repair", "approved", "delivered"),
            "repair": ("not-required", "changes-required", "repaired", "approved", "delivered"),
            "repair-blocked": ("not-required", "changes-required", "blocked", "blocked", "blocked"),
            "late-mixed": ("not-required", "changes-required", "blocked", "approved", "blocked"),
            "dirty-base": ("blocked", "blocked", "blocked", "blocked", "blocked"),
            "overflow": ("blocked", "blocked", "blocked", "blocked", "blocked"),
            "active-writer": ("blocked", "blocked", "blocked", "blocked", "blocked"),
            "large-issue": ("not-required", "approved", "no-repair", "approved", "delivered"),
            "self-writer": ("not-required", "approved", "no-repair", "approved", "delivered"),
            "unstable-reread": ("blocked", "blocked", "blocked", "blocked", "blocked"),
            "snapshot-mismatch": ("blocked", "blocked", "blocked", "blocked", "blocked"),
            "spurious-no-issues": ("blocked", "blocked", "blocked", "blocked", "blocked"),
            "mixed-state": ("blocked", "blocked", "blocked", "blocked", "blocked"),
            "final-rejected": ("not-required", "approved", "no-repair", "rejected", "blocked"),
            "digest-mismatch": ("not-required", "approved", "no-repair", "approved", "blocked"),
            "prepared-write-failure": ("not-required", "approved", "no-repair", "approved", "blocked"),
            "pending-reconciliation": ("not-required", "approved", "no-repair", "approved", "published-pending-reconciliation"),
            "recovery-only": ("not-required", "approved", "no-repair", "approved", "delivered"),
        }
        for scenario, expected in scenarios.items():
            with self.subTest(scenario=scenario), patch.dict(
                os.environ, {"FAKE_BATCH_SCENARIO": scenario}, clear=False
            ):
                before = set(self.data.glob("exec-runs/*/*/run.json"))
                code, _, stderr = self.invoke(
                    "run",
                    str(directory),
                    "--inputs",
                    str(directory / "example-inputs.json"),
                    "--max-calls",
                    "8",
                    "--allow-workspace-write",
                    "--allow-danger-full-access",
                    "--codex-bin",
                    str(self.fake_codex),
                )
                self.assertEqual(code, 0, stderr)
                created = set(self.data.glob("exec-runs/*/*/run.json")) - before
                self.assertEqual(len(created), 1)
                run_dir = created.pop().parent
                outputs = {
                    task_id: json.loads(
                        (run_dir / "tasks" / task_id / "final.json").read_text(encoding="utf-8")
                    )
                    for task_id in (
                        "inventory",
                        "plan-design",
                        "design-review",
                        "implement",
                        "adversarial-review",
                        "repair",
                        "final-review",
                        "deliver",
                    )
                }
                self.assertEqual(outputs["design-review"]["status"], expected[0])
                self.assertEqual(outputs["adversarial-review"]["status"], expected[1])
                self.assertEqual(outputs["repair"]["status"], expected[2])
                self.assertEqual(outputs["final-review"]["status"], expected[3])
                self.assertEqual(outputs["deliver"]["status"], expected[4])
                if scenario == "large-issue":
                    self.assertEqual(outputs["inventory"]["status"], "ready")
                    self.assertEqual(outputs["inventory"]["issues"][0]["title_bytes"], 78)
                    self.assertEqual(outputs["inventory"]["issues"][0]["body_bytes"], 22602)
                    self.assertEqual(outputs["inventory"]["aggregate_bytes"], 22680)
                if scenario == "overflow":
                    self.assertEqual(outputs["inventory"]["aggregate_bytes"], 262145)
                if scenario == "self-writer":
                    self.assertEqual(outputs["inventory"]["status"], "ready")
                if scenario == "active-writer":
                    self.assertEqual(outputs["inventory"]["status"], "blocked")
                    self.assertIn("different nonterminal same-repository", outputs["inventory"]["blockers"][0])
                if scenario == "overlap":
                    self.assertEqual(
                        [item["disposition"] for item in outputs["plan-design"]["items"]],
                        ["implement", "duplicate"],
                    )
                    self.assertEqual(
                        [item["number"] for item in outputs["deliver"]["issues"]],
                        [7, 8],
                    )
                    self.assertEqual(outputs["deliver"]["issues"][1]["depends_on"], [7])
                    self.assertEqual(outputs["deliver"]["issues"][1]["files"], ["fixture.py"])
                    self.assertEqual(outputs["deliver"]["issues"][1]["tests"], ["test_fixture"])
                if scenario == "repair":
                    self.assertEqual(
                        outputs["final-review"]["finding_dispositions"][0]["status"],
                        "resolved",
                    )
                    self.assertEqual(
                        outputs["deliver"]["reviewed_tree_digest"],
                        outputs["final-review"]["reviewed_tree_digest"],
                    )
                    self.assertEqual(
                        outputs["deliver"]["reviewed_diff_digest"],
                        outputs["final-review"]["reviewed_diff_digest"],
                    )
                    self.assertNotEqual(
                        outputs["implement"]["diff_digest"],
                        outputs["repair"]["diff_digest"],
                    )
                    self.assertEqual(
                        outputs["repair"]["diff_digest"],
                        outputs["final-review"]["reviewed_diff_digest"],
                    )
                if expected[4] == "blocked":
                    self.assertEqual(outputs["deliver"]["delivered_sha"], "")
                    self.assertTrue(outputs["deliver"]["blockers"])
                    self.assertEqual(outputs["deliver"]["recovery_phase"], "none")
                    completed_actions = {
                        item["action"]
                        for item in outputs["deliver"]["actions"]
                        if item["status"] == "completed"
                    }
                    self.assertNotIn("push", completed_actions)
                    self.assertNotIn("persist-published-pending", completed_actions)
                    self.assertTrue(
                        all(
                            value == "not-started"
                            for key, value in outputs["deliver"]["reconciliation"].items()
                            if key != "evidence"
                        )
                    )
                if scenario == "pending-reconciliation":
                    self.assertEqual(
                        outputs["deliver"]["reconciliation"]["remote"], "pending"
                    )
                    self.assertEqual(
                        [item["action"] for item in outputs["deliver"]["actions"]],
                        ["commit", "persist-prepared", "push", "persist-published-pending"],
                    )
                if scenario == "prepared-write-failure":
                    self.assertEqual(
                        [(item["action"], item["status"]) for item in outputs["deliver"]["actions"]],
                        [
                            ("commit", "completed"),
                            ("persist-prepared", "failed"),
                            ("push", "not-run"),
                        ],
                    )
                if scenario == "recovery-only":
                    recovery_actions = [item["action"] for item in outputs["deliver"]["actions"]]
                    self.assertEqual(recovery_actions[0], "recover-reconciliation")
                    self.assertNotIn("commit", recovery_actions)
                    self.assertNotIn("push", recovery_actions)
                rendered_deliver = (
                    run_dir / "tasks" / "deliver" / "prompt.txt"
                ).read_text(encoding="utf-8")
                self.assertIn(
                    f'"status": "{outputs["final-review"]["status"]}"',
                    rendered_deliver,
                )
                self.assertLess(
                    rendered_deliver.index("Stage and create exactly one batch commit"),
                    rendered_deliver.index("atomically persist phase prepared"),
                )
                self.assertLess(
                    rendered_deliver.index("atomically persist phase prepared"),
                    rendered_deliver.index("Non-force push only"),
                )
                self.assertLess(
                    rendered_deliver.index("atomically persist phase published-pending-reconciliation"),
                    rendered_deliver.index("Then idempotently verify remote"),
                )

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
