#!/usr/bin/env python3
"""Adversarial regression tests for the bundled documentation-scoring CLIs."""

from __future__ import annotations

import json
import hashlib
import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
SCAN = SCRIPT_DIR / "scan_documentation.py"
CALCULATE = SCRIPT_DIR / "calculate_score.py"
GRAPH = SCRIPT_DIR / "check_instruction_graph.py"
ARCHIVE = SCRIPT_DIR / "archive_instruction_content.py"


class ScoringScriptsTest(unittest.TestCase):
    def load_script_module(self, name: str, path: Path):
        spec = importlib.util.spec_from_file_location(name, path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def run_json(self, *command: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        completed = subprocess.run(command, text=True, capture_output=True, check=False)
        data = json.loads(completed.stdout) if completed.stdout else {}
        return completed, data

    def confirmed_node_provenance(self) -> dict:
        return {
            "assertion": "explicit",
            "status": "confirmed",
            "method": "manual_analysis",
            "adjudication": "manual_confirmed",
            "adjudicator": {
                "kind": "independent_agent",
                "id": "test-reviewer",
                "note": "Node normalization checked independently.",
            },
        }

    def template(self) -> dict:
        completed, review = self.run_json(sys.executable, str(CALCULATE), "--template")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return review

    def review_scope_digest(self, scope: dict) -> str:
        payload = {
            "purpose": scope["purpose"],
            "version": scope["version"],
            "complete": scope["complete"],
            "units": sorted(scope["units"], key=lambda item: item["path"]),
            "excluded": sorted(scope["excluded"]),
        }
        normalized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def graph_scope_digest(self, units: list[dict]) -> str:
        normalized = json.dumps(
            sorted(units, key=lambda item: item["path"]),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def valid_review(self) -> dict:
        review = self.template()
        content = b"# deploy.md\n"
        review["scope"] = {
            "purpose": "Проверить инструкцию деплоя.",
            "version": "v3.2",
            "complete": True,
            "units": [
                {
                    "path": "deploy.md",
                    "content_sha256": hashlib.sha256(content).hexdigest(),
                    "role": "core",
                    "profile": "runbook",
                }
            ],
            "excluded": [],
        }
        review["scope"]["digest"] = self.review_scope_digest(review["scope"])
        for criterion_id, item in review["criteria"].items():
            item["status"] = "met"
            item["rationale"] = "Критерий подтверждён в заданной области."
            item["evidence"] = [
                {
                    "kind": "supports",
                    "path": "deploy.md",
                    "locator": f"§ {criterion_id}",
                    "note": "Наблюдаемое подтверждение критерия.",
                }
            ]
        review["strengths"] = ["Проверяемый основной путь."]
        review["recommendations"] = ["Добавить второстепенный пример."]
        review["limitations"] = []
        return review

    def calculate(self, review: dict, *extra: str) -> tuple[subprocess.CompletedProcess[str], dict]:
        with tempfile.TemporaryDirectory() as directory:
            review = json.loads(json.dumps(review, ensure_ascii=False))
            root = Path(directory) / "scope"
            root.mkdir()
            for unit in review["scope"]["units"]:
                path = root / unit["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(f"# {unit['path']}\n", encoding="utf-8")
                unit["content_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            review["scope"]["digest"] = self.review_scope_digest(review["scope"])
            review_path = Path(directory) / "review.json"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            return self.run_json(
                sys.executable, str(CALCULATE), str(review_path), "--root", str(root), *extra
            )

    def graph(self, path: Path, sha256: str) -> dict:
        units = [{"path": path.name, "content_sha256": sha256}]
        digest = self.graph_scope_digest(units)
        source = {"path": path.name, "locator": "L1-L4", "note": "Точный span в scope unit."}
        return {
            "schema_version": "1.0",
            "mode": "semantic",
            "producer": "manual",
            "scope": {"digest": digest, "units": units},
            "nodes": [
                {
                    "id": "artifact.rules",
                    "type": "artifact",
                    "artifact_kind": "supporting",
                    "label": "Rules",
                    "authority": True,
                    "source": source,
                },
                {
                    "id": "rule.allow",
                    "type": "rule",
                    "label": "Разрешить изменение",
                    "provenance": self.confirmed_node_provenance(),
                    "claim": {
                        "subject": "agent",
                        "modality": "MUST",
                        "polarity": "positive",
                        "action": "edit",
                        "object": "protected file",
                        "condition": "explicit request",
                        "exception": None,
                    },
                    "source": source,
                },
                {
                    "id": "rule.deny",
                    "type": "rule",
                    "label": "Запретить изменение",
                    "provenance": self.confirmed_node_provenance(),
                    "claim": {
                        "subject": "agent",
                        "modality": "MUST",
                        "polarity": "negative",
                        "action": "edit",
                        "object": "protected file",
                        "condition": "explicit request",
                        "exception": None,
                    },
                    "source": source,
                },
            ],
            "edges": [
                {
                    "id": "edge.owner.allow",
                    "type": "owns",
                    "from": "artifact.rules",
                    "to": "rule.allow",
                    "assertion": "explicit",
                    "status": "confirmed",
                    "method": "manual_analysis",
                    "adjudication": "manual_confirmed",
                    "adjudicator": {
                        "kind": "independent_agent",
                        "id": "test-reviewer",
                        "note": "Source span checked independently.",
                    },
                    "evidence": [source],
                },
                {
                    "id": "edge.owner.deny",
                    "type": "owns",
                    "from": "artifact.rules",
                    "to": "rule.deny",
                    "assertion": "explicit",
                    "status": "confirmed",
                    "method": "manual_analysis",
                    "adjudication": "manual_confirmed",
                    "adjudicator": {
                        "kind": "independent_agent",
                        "id": "test-reviewer",
                        "note": "Source span checked independently.",
                    },
                    "evidence": [source],
                },
            ],
        }

    def test_full_evidence_scores_100(self) -> None:
        completed, result = self.calculate(self.valid_review())
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["confirmed_score"], 100.0)
        self.assertEqual(result["potential_score"], 100.0)
        self.assertEqual(result["confidence"], "high")

    def test_byte_exact_archive_round_trip_and_tamper_detection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            content = b"\x00# legacy\r\n```\n\xff\xfe\n"
            (root / "legacy.bin").write_bytes(content)
            added, result = self.run_json(
                sys.executable,
                str(ARCHIVE),
                "add",
                "--root",
                str(root),
                "--source",
                "legacy.bin",
                "--locator",
                "whole file before rename",
                "--content-kind",
                "mixed",
                "--reason",
                "canonical rename",
                "--target",
                "DOCS.md",
            )
            self.assertEqual(added.returncode, 0, added.stderr)
            archive_path = root / "archive" / "instructions.jsonl"
            evidence_bytes = archive_path.read_bytes()[
                result["evidence_start_byte"] : result["evidence_end_byte"]
            ]
            self.assertEqual(hashlib.sha256(evidence_bytes).hexdigest(), result["evidence_sha256"])
            verified, verification = self.run_json(
                sys.executable,
                str(ARCHIVE),
                "verify",
                "--root",
                str(root),
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(verification["records"], 1)
            extracted, _ = self.run_json(
                sys.executable,
                str(ARCHIVE),
                "extract",
                "--root",
                str(root),
                "--id",
                result["id"],
                "--output",
                "restored.bin",
            )
            self.assertEqual(extracted.returncode, 0, extracted.stderr)
            self.assertEqual((root / "restored.bin").read_bytes(), content)
            overwrite, _ = self.run_json(
                sys.executable,
                str(ARCHIVE),
                "extract",
                "--root",
                str(root),
                "--id",
                result["id"],
                "--output",
                "restored.bin",
            )
            self.assertEqual(overwrite.returncode, 2)

            span_added, span_result = self.run_json(
                sys.executable,
                str(ARCHIVE),
                "add",
                "--root",
                str(root),
                "--source",
                "legacy.bin",
                "--locator",
                "bytes 1:10 before line rewrite",
                "--content-kind",
                "normative",
                "--reason",
                "move to canonical owner",
                "--target",
                "MODEL.md#entity",
                "--start-byte",
                "1",
                "--end-byte",
                "10",
            )
            self.assertEqual(span_added.returncode, 0, span_added.stderr)
            span_extracted, _ = self.run_json(
                sys.executable,
                str(ARCHIVE),
                "extract",
                "--root",
                str(root),
                "--id",
                span_result["id"],
                "--output",
                "restored-span.bin",
            )
            self.assertEqual(span_extracted.returncode, 0, span_extracted.stderr)
            self.assertEqual((root / "restored-span.bin").read_bytes(), content[1:10])

            valid_archive = archive_path.read_bytes()
            head_path = root / span_result["head_path"]
            valid_head = head_path.read_bytes()
            first_line_end = valid_archive.index(b"\n") + 1
            archive_path.write_bytes(valid_archive[:first_line_end])
            truncated = subprocess.run(
                [sys.executable, str(ARCHIVE), "verify", "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(truncated.returncode, 2)
            archive_path.write_bytes(b"")
            emptied = subprocess.run(
                [sys.executable, str(ARCHIVE), "verify", "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(emptied.returncode, 2)
            archive_path.write_bytes(valid_archive)
            head_path.write_bytes(valid_head)
            restored_verify = subprocess.run(
                [sys.executable, str(ARCHIVE), "verify", "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(restored_verify.returncode, 0, restored_verify.stderr)

            records = [
                json.loads(line) for line in archive_path.read_text(encoding="utf-8").splitlines()
            ]
            records[0]["content_base64"] = "AA=="
            archive_path.write_text(
                "".join(
                    json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                    + "\n"
                    for record in records
                ),
                encoding="utf-8",
            )
            tampered = subprocess.run(
                [sys.executable, str(ARCHIVE), "verify", "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(tampered.returncode, 2)

    def test_archive_rejects_hardlink_and_symlink_sources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "scope"
            root.mkdir()
            outside = base / "outside.bin"
            outside.write_bytes(b"outside")
            hardlink = root / "hardlink.bin"
            os.link(outside, hardlink)

            def add(source: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    [
                        sys.executable,
                        str(ARCHIVE),
                        "add",
                        "--root",
                        str(root),
                        "--source",
                        source,
                        "--locator",
                        "whole file",
                        "--content-kind",
                        "mixed",
                        "--reason",
                        "test",
                        "--target",
                        "DOCS.md",
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            self.assertEqual(add("hardlink.bin").returncode, 2)
            symlink = root / "symlink.bin"
            try:
                symlink.symlink_to(outside)
            except OSError:
                return
            self.assertEqual(add("symlink.bin").returncode, 2)

    def test_archive_serializes_concurrent_appends(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "legacy.md").write_text("# Legacy\n", encoding="utf-8")

            def command(locator: str, target: str) -> list[str]:
                return [
                    sys.executable,
                    str(ARCHIVE),
                    "add",
                    "--root",
                    str(root),
                    "--source",
                    "legacy.md",
                    "--locator",
                    locator,
                    "--content-kind",
                    "mixed",
                    "--reason",
                    "concurrent migration",
                    "--target",
                    target,
                ]

            first = subprocess.Popen(
                command("whole file A", "DOCS.md"),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            second = subprocess.Popen(
                command("whole file B", "MODEL.md"),
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            first_stdout, first_stderr = first.communicate(timeout=10)
            second_stdout, second_stderr = second.communicate(timeout=10)
            self.assertEqual(first.returncode, 0, first_stderr or first_stdout)
            self.assertEqual(second.returncode, 0, second_stderr or second_stdout)
            verified, report = self.run_json(
                sys.executable,
                str(ARCHIVE),
                "verify",
                "--root",
                str(root),
            )
            self.assertEqual(verified.returncode, 0, verified.stderr)
            self.assertEqual(report["records"], 2)

    def test_cap_is_ceiling_without_penalty(self) -> None:
        review = self.valid_review()
        review["scope"]["units"] = [
            {"path": "AGENTS.md", "role": "authority", "profile": "instruction"},
            {"path": "DOCS.md", "role": "authority", "profile": "instruction"},
        ]
        for item in review["criteria"].values():
            item["evidence"][0]["path"] = "AGENTS.md"
        review["systemic_findings"] = [
            {
                "id": "edit-authority",
                "type": "critical_authority_conflict",
                "root_cause": "competing edit authority",
                "summary": "Два authority разрешают и запрещают одно изменение.",
                "evidence": [
                    {"kind": "conflict", "path": "AGENTS.md", "locator": "§ Editing", "note": "Разрешает."},
                    {"kind": "conflict", "path": "DOCS.md", "locator": "§ Protected", "note": "Запрещает."},
                ],
            }
        ]
        completed, result = self.calculate(review)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["raw_minimum"], 100.0)
        self.assertEqual(result["cap"], 39)
        self.assertEqual(result["confirmed_score"], 39.0)

    def test_unknown_produces_range_and_derived_confidence(self) -> None:
        review = self.valid_review()
        item = review["criteria"]["primary_source_alignment"]
        item["status"] = "unknown"
        item["rationale"] = "Первичный источник недоступен."
        item["evidence"] = [
            {
                "kind": "unavailable",
                "path": "https://example.invalid/provider-schema",
                "locator": "current schema",
                "note": "Источник назван, но недоступен в разрешённой области.",
            }
        ]
        completed, result = self.calculate(review)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(result["confirmed_score"], 95.0)
        self.assertEqual(result["potential_score"], 100.0)
        self.assertEqual(result["confidence"], "medium")

    def test_low_confidence_cannot_pass_gate(self) -> None:
        review = self.valid_review()
        for criterion_id in ("primary_source_alignment", "examples_commands_verified", "edges_errors"):
            item = review["criteria"][criterion_id]
            item["status"] = "unknown"
            item["rationale"] = "Источник недоступен."
            item["evidence"] = [
                {
                    "kind": "unavailable",
                    "path": f"https://example.invalid/{criterion_id}",
                    "locator": "current source",
                    "note": "Источник недоступен в разрешённой области.",
                }
            ]
        completed, _ = self.calculate(review, "--fail-below", "0")
        self.assertEqual(completed.returncode, 4)
        self.assertIn("низкая уверенность", completed.stderr)

    def test_gate_requires_verified_scope_and_rejects_external_support(self) -> None:
        review = self.valid_review()
        with tempfile.TemporaryDirectory() as directory:
            review_path = Path(directory) / "review.json"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            unverified = subprocess.run(
                [sys.executable, str(CALCULATE), str(review_path), "--fail-below", "100"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(unverified.returncode, 4)
            self.assertIn("scope hashes", unverified.stderr)

            for item in review["criteria"].values():
                item["evidence"][0]["path"] = "https://example.invalid/not-primary"
            review_path.unlink()
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            external = subprocess.run(
                [sys.executable, str(CALCULATE), str(review_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(external.returncode, 2)
            self.assertIn("URL разрешён только для unavailable", external.stderr)

            template_gate = subprocess.run(
                [sys.executable, str(CALCULATE), "--template", "--fail-below", "100"],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(template_gate.returncode, 2)
            self.assertIn("нельзя использовать с --template", template_gate.stderr)

    def test_manifest_template_hashes_real_files_and_gate_detects_change(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "scope"
            root.mkdir()
            document = root / "deploy.md"
            document.write_text("# Deploy\n", encoding="utf-8")
            review_path = Path(directory) / "review.json"
            created = subprocess.run(
                [
                    sys.executable,
                    str(CALCULATE),
                    "--template",
                    "--root",
                    str(root),
                    "--purpose",
                    "Проверить deploy",
                    "--version",
                    "commit-1",
                    "--unit",
                    "deploy.md",
                    "core",
                    "runbook",
                    "--output",
                    str(review_path),
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(created.returncode, 0, created.stderr)
            review = json.loads(review_path.read_text(encoding="utf-8"))
            self.assertEqual(
                review["scope"]["units"][0]["content_sha256"],
                hashlib.sha256(document.read_bytes()).hexdigest(),
            )
            document.write_text("# Changed\n", encoding="utf-8")
            changed = subprocess.run(
                [sys.executable, str(CALCULATE), str(review_path), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(changed.returncode, 2)
            self.assertIn("Content SHA-256", changed.stderr)

    def test_non_finite_gate_threshold_is_rejected(self) -> None:
        review = self.valid_review()
        with tempfile.TemporaryDirectory() as directory:
            review_path = Path(directory) / "review.json"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [sys.executable, str(CALCULATE), str(review_path), "--fail-below", "nan"],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(completed.returncode, 2)

    def test_json_nan_and_placeholders_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            invalid_path = Path(directory) / "invalid.json"
            invalid_path.write_text('{"scope": NaN}', encoding="utf-8")
            invalid = subprocess.run(
                [sys.executable, str(CALCULATE), str(invalid_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(invalid.returncode, 2)
            template_path = Path(directory) / "template.json"
            template_path.write_text(json.dumps(self.template(), ensure_ascii=False), encoding="utf-8")
            placeholder = subprocess.run(
                [sys.executable, str(CALCULATE), str(template_path)],
                text=True,
                capture_output=True,
                check=False,
            )
        self.assertEqual(placeholder.returncode, 2)
        self.assertIn("placeholder", placeholder.stderr.lower())

    def test_calculator_never_clobbers_input_or_existing_output(self) -> None:
        review = self.valid_review()
        with tempfile.TemporaryDirectory() as directory:
            review_path = Path(directory) / "review.json"
            original = json.dumps(review, ensure_ascii=False)
            review_path.write_text(original, encoding="utf-8")
            same = subprocess.run(
                [sys.executable, str(CALCULATE), str(review_path), "--output", str(review_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(same.returncode, 2)
            self.assertEqual(review_path.read_text(encoding="utf-8"), original)
            output_path = Path(directory) / "result.json"
            output_path.write_text("keep", encoding="utf-8")
            existing = subprocess.run(
                [sys.executable, str(CALCULATE), str(review_path), "--output", str(output_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(existing.returncode, 2)
            self.assertEqual(output_path.read_text(encoding="utf-8"), "keep")

    def test_scan_ignores_fenced_code_and_reports_real_findings(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text(
                "```markdown\n# Fake\nTODO\n[bad](none.md)\n```\n\nVisible text.\n",
                encoding="utf-8",
            )
            completed, report = self.run_json(sys.executable, str(SCAN), str(root))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            codes = {item["code"] for item in report["findings"]}
            self.assertIn("missing-h1", codes)
            self.assertNotIn("unfinished-marker", codes)
            self.assertNotIn("broken-relative-link", codes)

    def test_scan_does_not_follow_symlinks_or_clobber_input(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            outside = root / "outside.md"
            outside.write_text("# Secret\n", encoding="utf-8")
            scoped = root / "scoped"
            scoped.mkdir()
            source = scoped / "source.md"
            source.write_text("# Source\n\n[external](../outside.md#secret)\n", encoding="utf-8")
            symlink = scoped / "link.md"
            try:
                symlink.symlink_to(outside)
            except OSError:
                self.skipTest("Символические ссылки недоступны в окружении")
            completed, report = self.run_json(sys.executable, str(SCAN), str(scoped))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual([item["path"] for item in report["files"]], ["source.md"])
            direct = subprocess.run(
                [sys.executable, str(SCAN), str(symlink)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(direct.returncode, 2)
            original = source.read_text(encoding="utf-8")
            clobber = subprocess.run(
                [sys.executable, str(SCAN), str(source), "--output", str(source)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(clobber.returncode, 2)
            self.assertEqual(source.read_text(encoding="utf-8"), original)

    def test_graph_reports_semantic_conflict_as_candidate_only(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "rules.md"
            document.write_text("# Rules\n\nAllow.\nDeny.\n", encoding="utf-8")
            sha256 = hashlib.sha256(document.read_bytes()).hexdigest()
            graph_path = root / "graph.json"
            graph_path.write_text(json.dumps(self.graph(document, sha256), ensure_ascii=False), encoding="utf-8")
            completed, report = self.run_json(
                sys.executable, str(GRAPH), str(graph_path), "--root", str(root), "--fail-on-confirmed"
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(report["metrics"]["confirmed_findings"], 2)
            self.assertEqual(report["metrics"]["candidate_findings"], 1)
            self.assertIn(
                "normalized-rule-conflict",
                {item["code"] for item in report["findings"] if item["status"] == "candidate"},
            )

    def test_independent_finding_resolution_unblocks_migration_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "rules.md"
            document.write_text("# Rules\n\nAllow.\nDeny.\n", encoding="utf-8")
            sha256 = hashlib.sha256(document.read_bytes()).hexdigest()
            graph = self.graph(document, sha256)
            source = graph["nodes"][0]["source"]
            graph["nodes"].append(
                {"id": "scope.repo", "type": "scope", "label": "Repository", "source": source}
            )
            for rule_id in ("rule.allow", "rule.deny"):
                graph["edges"].append(
                    {
                        "id": f"edge.{rule_id}.scope",
                        "type": "applies_to",
                        "from": rule_id,
                        "to": "scope.repo",
                        "assertion": "explicit",
                        "status": "confirmed",
                        "method": "manual_analysis",
                        "adjudication": "manual_confirmed",
                        "adjudicator": {
                            "kind": "independent_agent",
                            "id": "test-reviewer",
                            "note": "Scope checked independently.",
                        },
                        "evidence": [source],
                    }
                )
            graph["edges"].append(
                {
                    "id": "edge.allow.overrides.deny",
                    "type": "overrides",
                    "from": "rule.allow",
                    "to": "rule.deny",
                    "assertion": "explicit",
                    "status": "confirmed",
                    "method": "manual_analysis",
                    "adjudication": "manual_confirmed",
                    "adjudicator": {
                        "kind": "independent_agent",
                        "id": "test-reviewer",
                        "note": "Explicit override checked independently.",
                    },
                    "evidence": [source],
                }
            )
            archive_path = root / "archive" / "instructions.jsonl"
            archive_path.parent.mkdir()
            archive_record = b"record-one\n"
            archive_path.write_bytes(archive_record)
            graph["scope"]["units"].append(
                {
                    "path": "archive/instructions.jsonl",
                    "content_sha256": hashlib.sha256(archive_record).hexdigest(),
                }
            )
            graph["scope"]["digest"] = self.graph_scope_digest(graph["scope"]["units"])
            archive_evidence = {
                "path": "archive/instructions.jsonl",
                "locator": "record-one",
                "note": "Immutable archive record line.",
                "start_byte": 0,
                "end_byte": len(archive_record),
                "evidence_sha256": hashlib.sha256(archive_record).hexdigest(),
            }
            graph["nodes"].append(
                {
                    "id": "source.archive.record",
                    "type": "source",
                    "label": "Archived source record",
                    "authority": False,
                    "source": archive_evidence,
                }
            )
            graph["edges"].append(
                {
                    "id": "edge.allow.archive",
                    "type": "derives_from",
                    "from": "rule.allow",
                    "to": "source.archive.record",
                    "assertion": "explicit",
                    "status": "confirmed",
                    "method": "manual_analysis",
                    "adjudication": "manual_confirmed",
                    "adjudicator": {
                        "kind": "independent_agent",
                        "id": "test-reviewer",
                        "note": "Archive lineage checked independently.",
                    },
                    "evidence": [archive_evidence],
                }
            )

            def run(value: dict) -> tuple[subprocess.CompletedProcess[str], dict]:
                graph_path = root / "graph.json"
                graph_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                return self.run_json(
                    sys.executable,
                    str(GRAPH),
                    str(graph_path),
                    "--root",
                    str(root),
                    "--migration-gate",
                )

            unresolved, unresolved_report = run(graph)
            self.assertEqual(unresolved.returncode, 4)
            candidate = next(
                item
                for item in unresolved_report["findings"]
                if item["code"] == "normalized-rule-conflict"
            )

            graph["finding_resolutions"] = [
                {
                    "id": "resolution.allow-deny",
                    "finding_code": "normalized-rule-conflict",
                    "nodes": ["rule.allow", "rule.deny"],
                    "candidate_fingerprint": candidate["candidate_fingerprint"],
                    "decision": "resolved",
                    "method": "manual_analysis",
                    "adjudicator": {
                        "kind": "independent_agent",
                        "id": "test-reviewer",
                        "note": "Confirmed scopes and override semantics resolve the apparent conflict.",
                    },
                    "evidence": [source],
                }
            ]
            resolved, report = run(graph)
            self.assertEqual(resolved.returncode, 0, resolved.stderr)
            self.assertEqual(report["metrics"]["candidate_findings"], 0)
            self.assertEqual(report["metrics"]["resolved_findings"], 1)

            archive_path.write_bytes(archive_record + b"record-two\n")
            archive_unit = next(
                unit
                for unit in graph["scope"]["units"]
                if unit["path"] == "archive/instructions.jsonl"
            )
            archive_unit["content_sha256"] = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            graph["scope"]["digest"] = self.graph_scope_digest(graph["scope"]["units"])
            archive_appended, _ = run(graph)
            self.assertEqual(archive_appended.returncode, 0, archive_appended.stderr)

            independent_branch = json.loads(json.dumps(graph))
            unrelated_path = root / "unrelated.md"
            unrelated_path.write_text("# Unrelated\n", encoding="utf-8")
            independent_branch["scope"]["units"].append(
                {
                    "path": "unrelated.md",
                    "content_sha256": hashlib.sha256(unrelated_path.read_bytes()).hexdigest(),
                }
            )
            independent_branch["scope"]["digest"] = self.graph_scope_digest(
                independent_branch["scope"]["units"]
            )
            independent_branch["nodes"].append(
                {
                    "id": "artifact.unrelated",
                    "type": "artifact",
                    "artifact_kind": "supporting",
                    "label": "Unrelated",
                    "authority": False,
                    "source": {
                        "path": "unrelated.md",
                        "locator": "L1",
                        "note": "Independent branch.",
                    },
                }
            )
            branch_result, _ = run(independent_branch)
            self.assertEqual(branch_result.returncode, 0, branch_result.stderr)

            mutual = json.loads(json.dumps(graph))
            del mutual["finding_resolutions"]
            reverse = json.loads(
                json.dumps(next(edge for edge in mutual["edges"] if edge["type"] == "overrides"))
            )
            reverse.update(
                {
                    "id": "edge.deny.overrides.allow",
                    "from": "rule.deny",
                    "to": "rule.allow",
                }
            )
            mutual["edges"].append(reverse)
            mutual_unresolved, mutual_report = run(mutual)
            self.assertEqual(mutual_unresolved.returncode, 4)
            mutual_candidate = next(
                item
                for item in mutual_report["findings"]
                if item["code"] == "normalized-rule-conflict"
            )
            mutual["finding_resolutions"] = json.loads(json.dumps(graph["finding_resolutions"]))
            mutual["finding_resolutions"][0]["candidate_fingerprint"] = mutual_candidate[
                "candidate_fingerprint"
            ]
            mutual_resolved, mutual_resolved_report = run(mutual)
            self.assertEqual(mutual_resolved.returncode, 4)
            self.assertIn(
                "override-cycle",
                {item["code"] for item in mutual_resolved_report["findings"]},
            )

            stale = json.loads(json.dumps(graph))
            next(node for node in stale["nodes"] if node["id"] == "rule.allow")["label"] += " changed"
            stale_result, _ = run(stale)
            self.assertEqual(stale_result.returncode, 2)

    def test_migration_gate_is_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "rules.md"
            document.write_text("# Rules\n\nAllow.\n", encoding="utf-8")
            sha256 = hashlib.sha256(document.read_bytes()).hexdigest()
            graph = self.graph(document, sha256)
            source = graph["nodes"][0]["source"]
            graph["nodes"] = [graph["nodes"][0], graph["nodes"][1]]
            graph["nodes"].append(
                {"id": "scope.repo", "type": "scope", "label": "Repository", "source": source}
            )
            graph["edges"] = [graph["edges"][0]]
            applies = {
                "id": "edge.allow.scope",
                "type": "applies_to",
                "from": "rule.allow",
                "to": "scope.repo",
                "assertion": "explicit",
                "status": "confirmed",
                "method": "manual_analysis",
                "adjudication": "manual_confirmed",
                "adjudicator": {
                    "kind": "independent_agent",
                    "id": "test-reviewer",
                    "note": "Scope checked independently.",
                },
                "evidence": [source],
            }
            graph["edges"].append(applies)

            def run(
                value: dict, gate: str = "--migration-gate"
            ) -> subprocess.CompletedProcess[str]:
                graph_path = root / "graph.json"
                graph_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                return subprocess.run(
                    [
                        sys.executable,
                        str(GRAPH),
                        str(graph_path),
                        "--root",
                        str(root),
                        gate,
                    ],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            self.assertEqual(run(graph).returncode, 0)
            self.assertEqual(run(graph, "--final-topology-gate").returncode, 4)

            unadjudicated_node = json.loads(json.dumps(graph))
            next(
                node for node in unadjudicated_node["nodes"] if node["id"] == "rule.allow"
            )["provenance"] = {
                "assertion": "inferred",
                "status": "candidate",
                "method": "llm",
                "adjudication": "not_reviewed",
                "adjudicator": {
                    "kind": "none",
                    "id": "none",
                    "note": "Awaiting independent review.",
                },
            }
            self.assertEqual(run(unadjudicated_node).returncode, 4)

            no_scope = json.loads(json.dumps(graph))
            no_scope["edges"] = [edge for edge in no_scope["edges"] if edge["type"] != "applies_to"]
            self.assertEqual(run(no_scope).returncode, 4)

            no_owner = json.loads(json.dumps(graph))
            no_owner["edges"] = [edge for edge in no_owner["edges"] if edge["type"] != "owns"]
            self.assertEqual(run(no_owner).returncode, 4)

            candidate = json.loads(json.dumps(graph))
            candidate["edges"].append(
                {
                    "id": "edge.candidate.reference",
                    "type": "references",
                    "from": "artifact.rules",
                    "to": "scope.repo",
                    "assertion": "inferred",
                    "status": "candidate",
                    "method": "llm",
                    "adjudication": "not_reviewed",
                    "adjudicator": {
                        "kind": "none",
                        "id": "none",
                        "note": "Not reviewed.",
                    },
                    "evidence": [source],
                }
            )
            self.assertEqual(run(candidate).returncode, 4)

            bad_name = json.loads(json.dumps(graph))
            contract_dir = root / "contracts"
            contract_dir.mkdir()
            bad_contract = contract_dir / "ARTIFACTS.md"
            bad_contract.write_bytes(document.read_bytes())
            bad_path = "contracts/ARTIFACTS.md"
            bad_name["scope"]["units"][0]["path"] = bad_path
            normalized = json.dumps(
                bad_name["scope"]["units"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            bad_name["scope"]["digest"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            for node in bad_name["nodes"]:
                node["source"]["path"] = bad_path
                if node["id"] == "artifact.rules":
                    node["artifact_kind"] = "contract"
            for edge in bad_name["edges"]:
                for evidence in edge["evidence"]:
                    evidence["path"] = bad_path
            self.assertEqual(run(bad_name).returncode, 4)

            good_name = json.loads(json.dumps(bad_name))
            good_contract = contract_dir / "artifacts.md"
            good_contract.write_bytes(document.read_bytes())
            good_path = "contracts/artifacts.md"
            good_name["scope"]["units"][0]["path"] = good_path
            normalized = json.dumps(
                good_name["scope"]["units"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            good_name["scope"]["digest"] = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
            for node in good_name["nodes"]:
                node["source"]["path"] = good_path
            for edge in good_name["edges"]:
                for evidence in edge["evidence"]:
                    evidence["path"] = good_path
            self.assertEqual(run(good_name).returncode, 0)

    def test_polarity_resolution_does_not_hide_modality_drift(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "rules.md"
            document.write_text("# Rules\n\nAllow.\nDeny.\nShould allow.\n", encoding="utf-8")
            graph = self.graph(document, hashlib.sha256(document.read_bytes()).hexdigest())
            source = graph["nodes"][0]["source"]
            should_rule = json.loads(
                json.dumps(next(node for node in graph["nodes"] if node["id"] == "rule.allow"))
            )
            should_rule.update({"id": "rule.should", "label": "Следует разрешить изменение"})
            should_rule["claim"]["modality"] = "SHOULD"
            graph["nodes"].extend(
                [
                    should_rule,
                    {"id": "scope.repo", "type": "scope", "label": "Repository", "source": source},
                ]
            )
            owner = json.loads(json.dumps(graph["edges"][0]))
            owner.update({"id": "edge.owner.should", "to": "rule.should"})
            graph["edges"].append(owner)
            for rule_id in ("rule.allow", "rule.deny", "rule.should"):
                graph["edges"].append(
                    {
                        "id": f"edge.{rule_id}.scope",
                        "type": "applies_to",
                        "from": rule_id,
                        "to": "scope.repo",
                        "assertion": "explicit",
                        "status": "confirmed",
                        "method": "manual_analysis",
                        "adjudication": "manual_confirmed",
                        "adjudicator": {
                            "kind": "independent_agent",
                            "id": "test-reviewer",
                            "note": "Scope checked independently.",
                        },
                        "evidence": [source],
                    }
                )
            for edge_id, from_id, to_id in (
                ("edge.allow.overrides.deny", "rule.allow", "rule.deny"),
                ("edge.deny.overrides.should", "rule.deny", "rule.should"),
            ):
                graph["edges"].append(
                    {
                        "id": edge_id,
                        "type": "overrides",
                        "from": from_id,
                        "to": to_id,
                        "assertion": "explicit",
                        "status": "confirmed",
                        "method": "manual_analysis",
                        "adjudication": "manual_confirmed",
                        "adjudicator": {
                            "kind": "independent_agent",
                            "id": "test-reviewer",
                            "note": "Override checked independently.",
                        },
                        "evidence": [source],
                    }
                )

            graph_path = root / "graph.json"
            def run(value: dict) -> tuple[subprocess.CompletedProcess[str], dict]:
                graph_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                return self.run_json(
                    sys.executable,
                    str(GRAPH),
                    str(graph_path),
                    "--root",
                    str(root),
                    "--migration-gate",
                )

            unresolved, report = run(graph)
            self.assertEqual(unresolved.returncode, 4)
            candidates = {
                item["code"]: item for item in report["findings"] if item["status"] == "candidate"
            }
            self.assertEqual(
                set(candidates), {"normalized-rule-conflict", "normalized-modality-drift"}
            )
            graph["finding_resolutions"] = [
                {
                    "id": "resolution.polarity",
                    "finding_code": "normalized-rule-conflict",
                    "nodes": ["rule.allow", "rule.deny", "rule.should"],
                    "candidate_fingerprint": candidates["normalized-rule-conflict"][
                        "candidate_fingerprint"
                    ],
                    "decision": "resolved",
                    "method": "manual_analysis",
                    "adjudicator": {
                        "kind": "independent_agent",
                        "id": "test-reviewer",
                        "note": "Polarity pairs have directed overrides.",
                    },
                    "evidence": [source],
                }
            ]
            partially_resolved, report = run(graph)
            self.assertEqual(partially_resolved.returncode, 4)
            statuses = {item["code"]: item["status"] for item in report["findings"]}
            self.assertEqual(statuses["normalized-rule-conflict"], "resolved")
            self.assertEqual(statuses["normalized-modality-drift"], "candidate")

    def test_final_topology_gate_requires_root_artifacts_and_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            root_files = ["AGENTS.md", "DOCS.md", "MODEL.md", "WORKFLOW.md"]
            units = []
            nodes = []
            for filename in root_files:
                path = root / filename
                path.write_text(f"# {filename}\n", encoding="utf-8")
                units.append(
                    {"path": filename, "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                )
                nodes.append(
                    {
                        "id": f"artifact.{filename.removesuffix('.md').lower()}",
                        "type": "artifact",
                        "artifact_kind": "root_instruction",
                        "label": filename,
                        "authority": True,
                        "source": {"path": filename, "locator": "L1", "note": "Canonical root."},
                    }
                )
            contract_path = root / "contracts" / "artifacts.md"
            contract_path.parent.mkdir()
            contract_path.write_text("# Artifact contract\n", encoding="utf-8")
            units.append(
                {
                    "path": "contracts/artifacts.md",
                    "content_sha256": hashlib.sha256(contract_path.read_bytes()).hexdigest(),
                }
            )
            nodes.append(
                {
                    "id": "artifact.contract.artifacts",
                    "type": "artifact",
                    "artifact_kind": "contract",
                    "label": "Artifact contract",
                    "authority": True,
                    "source": {
                        "path": "contracts/artifacts.md",
                        "locator": "L1",
                        "note": "Canonical contract.",
                    },
                }
            )
            normalized = json.dumps(
                sorted(units, key=lambda item: item["path"]),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            edges = []
            for edge_id, source_id, target_id, source_path in (
                ("edge.agents.docs", "artifact.agents", "artifact.docs", "AGENTS.md"),
                ("edge.docs.model", "artifact.docs", "artifact.model", "DOCS.md"),
                ("edge.docs.workflow", "artifact.docs", "artifact.workflow", "DOCS.md"),
                (
                    "edge.model.artifacts",
                    "artifact.model",
                    "artifact.contract.artifacts",
                    "MODEL.md",
                ),
            ):
                evidence = {"path": source_path, "locator": "L1", "note": "Routing link."}
                edges.append(
                    {
                        "id": edge_id,
                        "type": "routes_to",
                        "from": source_id,
                        "to": target_id,
                        "assertion": "explicit",
                        "status": "confirmed",
                        "method": "manual_analysis",
                        "adjudication": "manual_confirmed",
                        "adjudicator": {
                            "kind": "independent_agent",
                            "id": "test-reviewer",
                            "note": "Routing checked independently.",
                        },
                        "evidence": [evidence],
                    }
                )
            graph = {
                "schema_version": "1.0",
                "mode": "explicit",
                "producer": "manual",
                "scope": {
                    "digest": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
                    "units": units,
                },
                "nodes": nodes,
                "edges": edges,
            }
            graph_path = root / "graph.json"
            def run(value: dict, gate: str = "--final-topology-gate") -> subprocess.CompletedProcess[str]:
                graph_path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
                return subprocess.run(
                    [sys.executable, str(GRAPH), str(graph_path), "--root", str(root), gate],
                    text=True,
                    capture_output=True,
                    check=False,
                )

            completed = run(graph)
            self.assertEqual(completed.returncode, 0, completed.stderr)

            missing_parent_graph = json.loads(json.dumps(graph))
            missing_parent_graph["edges"] = [
                edge
                for edge in missing_parent_graph["edges"]
                if edge["id"] != "edge.model.artifacts"
            ]
            self.assertEqual(run(missing_parent_graph).returncode, 4)

            cross_reference = json.loads(json.dumps(missing_parent_graph))
            reference = json.loads(
                json.dumps(next(edge for edge in graph["edges"] if edge["id"] == "edge.model.artifacts"))
            )
            reference["type"] = "references"
            cross_reference["edges"].append(reference)
            self.assertEqual(run(cross_reference).returncode, 4)

            cycle = json.loads(json.dumps(graph))
            back_route = json.loads(json.dumps(cycle["edges"][-1]))
            back_route.update(
                {
                    "id": "edge.artifacts.model",
                    "from": "artifact.contract.artifacts",
                    "to": "artifact.model",
                    "evidence": [
                        {
                            "path": "contracts/artifacts.md",
                            "locator": "L1",
                            "note": "Invalid back route.",
                        }
                    ],
                }
            )
            cycle["edges"].append(back_route)
            self.assertEqual(run(cycle, "--migration-gate").returncode, 4)

            extra_root_parent = json.loads(json.dumps(graph))
            extra_route = json.loads(json.dumps(extra_root_parent["edges"][0]))
            extra_route.update(
                {
                    "id": "edge.agents.model",
                    "from": "artifact.agents",
                    "to": "artifact.model",
                }
            )
            extra_root_parent["edges"].append(extra_route)
            self.assertEqual(run(extra_root_parent).returncode, 4)

            agents_contract = json.loads(json.dumps(graph))
            extra_contract_route = json.loads(json.dumps(agents_contract["edges"][0]))
            extra_contract_route.update(
                {
                    "id": "edge.agents.artifacts",
                    "from": "artifact.agents",
                    "to": "artifact.contract.artifacts",
                }
            )
            agents_contract["edges"].append(extra_contract_route)
            self.assertEqual(run(agents_contract).returncode, 4)

            self_route = json.loads(json.dumps(graph))
            loop = json.loads(json.dumps(self_route["edges"][-1]))
            loop.update(
                {
                    "id": "edge.artifacts.self",
                    "from": "artifact.contract.artifacts",
                    "to": "artifact.contract.artifacts",
                }
            )
            self_route["edges"].append(loop)
            self.assertEqual(run(self_route).returncode, 2)

            missing_artifact = json.loads(json.dumps(graph))
            orphan_path = root / "contracts" / "orphan.md"
            orphan_path.write_text("# Orphan contract\n", encoding="utf-8")
            missing_artifact["scope"]["units"].append(
                {
                    "path": "contracts/orphan.md",
                    "content_sha256": hashlib.sha256(orphan_path.read_bytes()).hexdigest(),
                }
            )
            normalized_units = json.dumps(
                sorted(missing_artifact["scope"]["units"], key=lambda item: item["path"]),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            missing_artifact["scope"]["digest"] = hashlib.sha256(
                normalized_units.encode("utf-8")
            ).hexdigest()
            self.assertEqual(run(missing_artifact).returncode, 4)

            duplicate_artifact = json.loads(json.dumps(graph))
            duplicate = json.loads(json.dumps(duplicate_artifact["nodes"][-1]))
            duplicate["id"] = "artifact.contract.artifacts.duplicate"
            duplicate_artifact["nodes"].append(duplicate)
            self.assertEqual(run(duplicate_artifact).returncode, 4)

    def test_graph_enforces_relation_types_hashes_and_confirmed_gate(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "rules.md"
            document.write_text("# Rules\n", encoding="utf-8")
            sha256 = hashlib.sha256(document.read_bytes()).hexdigest()
            graph = self.graph(document, sha256)
            source = graph["nodes"][0]["source"]
            graph["nodes"].append(
                {
                    "id": "artifact.second",
                    "type": "artifact",
                    "artifact_kind": "supporting",
                    "label": "Second",
                    "authority": True,
                    "source": source,
                }
            )
            graph["edges"].extend(
                [
                    {
                        "id": "edge.second.owner",
                        "type": "owns",
                        "from": "artifact.second",
                        "to": "rule.allow",
                        "assertion": "explicit",
                        "status": "confirmed",
                        "method": "manual_analysis",
                        "adjudication": "manual_confirmed",
                        "adjudicator": {
                            "kind": "independent_agent",
                            "id": "test-reviewer",
                            "note": "Source span checked independently.",
                        },
                        "evidence": [source],
                    },
                    {
                        "id": "edge.a.precedes.b",
                        "type": "precedes",
                        "from": "artifact.rules",
                        "to": "artifact.second",
                        "assertion": "explicit",
                        "status": "confirmed",
                        "method": "manual_analysis",
                        "adjudication": "manual_confirmed",
                        "adjudicator": {
                            "kind": "independent_agent",
                            "id": "test-reviewer",
                            "note": "Source span checked independently.",
                        },
                        "evidence": [source],
                    },
                    {
                        "id": "edge.b.precedes.a",
                        "type": "precedes",
                        "from": "artifact.second",
                        "to": "artifact.rules",
                        "assertion": "explicit",
                        "status": "confirmed",
                        "method": "manual_analysis",
                        "adjudication": "manual_confirmed",
                        "adjudicator": {
                            "kind": "independent_agent",
                            "id": "test-reviewer",
                            "note": "Source span checked independently.",
                        },
                        "evidence": [source],
                    },
                ]
            )
            graph_path = root / "graph.json"
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            gated, report = self.run_json(
                sys.executable, str(GRAPH), str(graph_path), "--root", str(root), "--fail-on-confirmed"
            )
            self.assertEqual(gated.returncode, 4, gated.stderr)
            codes = {item["code"] for item in report["findings"]}
            self.assertIn("multiple-rule-owners", codes)
            self.assertIn("precedence-cycle", codes)

            graph["edges"][0]["type"] = "precedes"
            graph_path.unlink()
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            malformed = subprocess.run(
                [sys.executable, str(GRAPH), str(graph_path), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(malformed.returncode, 2)
            self.assertIn("не допускает", malformed.stderr)

            graph = self.graph(document, "0" * 64)
            graph_path.unlink()
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            mismatch = subprocess.run(
                [sys.executable, str(GRAPH), str(graph_path), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(mismatch.returncode, 2)
            self.assertIn("SHA-256", mismatch.stderr)

            graph = self.graph(document, sha256)
            graph["nodes"][1]["source"] = {
                "path": "rules.md",
                "locator": "byte 0",
                "note": "Incorrect caller-supplied bound hash.",
                "start_byte": 0,
                "end_byte": 1,
                "evidence_sha256": "0" * 64,
            }
            graph_path.unlink()
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            false_bound = subprocess.run(
                [sys.executable, str(GRAPH), str(graph_path), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(false_bound.returncode, 2)
            self.assertIn("evidence_sha256", false_bound.stderr)

    def test_graph_rejects_model_self_confirmation_and_duplicate_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "rules.md"
            document.write_text("# Rules\n", encoding="utf-8")
            graph = self.graph(document, hashlib.sha256(document.read_bytes()).hexdigest())
            graph["producer"] = "model_assisted"
            graph["edges"][0]["assertion"] = "inferred"
            graph["edges"][0]["method"] = "nli"
            graph["edges"][0]["adjudication"] = "deterministic_confirmed"
            graph_path = root / "graph.json"
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            self_confirmed = subprocess.run(
                [sys.executable, str(GRAPH), str(graph_path), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(self_confirmed.returncode, 2)
            self.assertIn("inferred edge не может быть confirmed", self_confirmed.stderr)

            graph = self.graph(document, hashlib.sha256(document.read_bytes()).hexdigest())
            source = graph["nodes"][0]["source"]
            graph["nodes"].append(
                {
                    "id": "entity.task",
                    "type": "entity",
                    "label": "Task",
                    "provenance": self.confirmed_node_provenance(),
                    "canonical_name": "Task",
                    "aliases": ["work item", "WORK ITEM"],
                    "source": source,
                }
            )
            graph_path.unlink()
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            duplicate_alias = subprocess.run(
                [sys.executable, str(GRAPH), str(graph_path), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(duplicate_alias.returncode, 2)
            self.assertIn("aliases содержит дубликаты", duplicate_alias.stderr)

    def test_graph_handles_malformed_endpoint_and_unicode_entity_collision(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "rules.md"
            document.write_text("# Rules\n", encoding="utf-8")
            sha256 = hashlib.sha256(document.read_bytes()).hexdigest()
            graph = self.graph(document, sha256)
            del graph["nodes"][0]["type"]
            graph_path = root / "graph.json"
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            malformed = subprocess.run(
                [sys.executable, str(GRAPH), str(graph_path), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(malformed.returncode, 2)
            self.assertIn("type", malformed.stderr)

            graph = self.graph(document, sha256)
            source = graph["nodes"][0]["source"]
            graph["nodes"].extend(
                [
                    {
                        "id": "entity.cafe.one",
                        "type": "entity",
                        "label": "Cafe one",
                        "provenance": self.confirmed_node_provenance(),
                        "canonical_name": "Café",
                        "aliases": [],
                        "source": source,
                    },
                    {
                        "id": "entity.cafe.two",
                        "type": "entity",
                        "label": "Cafe two",
                        "provenance": self.confirmed_node_provenance(),
                        "canonical_name": "Cafe\u0301",
                        "aliases": [],
                        "source": source,
                    },
                ]
            )
            graph_path.unlink()
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            collision, report = self.run_json(
                sys.executable, str(GRAPH), str(graph_path), "--root", str(root), "--fail-on-confirmed"
            )
            self.assertEqual(collision.returncode, 4, collision.stderr)
            self.assertIn("entity-name-collision", {item["code"] for item in report["findings"]})

            graph = self.graph(document, sha256)
            graph["nodes"].extend(
                [
                    {
                        "id": "entity.alias",
                        "type": "entity",
                        "label": "Alias",
                        "provenance": self.confirmed_node_provenance(),
                        "canonical_name": "Work item alias",
                        "aliases": [],
                        "source": source,
                    },
                    {
                        "id": "entity.task",
                        "type": "entity",
                        "label": "Task",
                        "provenance": self.confirmed_node_provenance(),
                        "canonical_name": "Task",
                        "aliases": [],
                        "source": source,
                    },
                    {
                        "id": "entity.issue",
                        "type": "entity",
                        "label": "Issue",
                        "provenance": self.confirmed_node_provenance(),
                        "canonical_name": "Issue",
                        "aliases": [],
                        "source": source,
                    },
                ]
            )
            adjudicator = {
                "kind": "independent_agent",
                "id": "test-reviewer",
                "note": "Mapping checked independently.",
            }
            for edge_id, target in (("edge.alias.task", "entity.task"), ("edge.alias.issue", "entity.issue")):
                graph["edges"].append(
                    {
                        "id": edge_id,
                        "type": "aliases",
                        "from": "entity.alias",
                        "to": target,
                        "assertion": "explicit",
                        "status": "confirmed",
                        "method": "manual_analysis",
                        "adjudication": "manual_confirmed",
                        "adjudicator": adjudicator,
                        "evidence": [source],
                    }
                )
            graph_path.unlink()
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            ambiguous, report = self.run_json(
                sys.executable, str(GRAPH), str(graph_path), "--root", str(root), "--fail-on-confirmed"
            )
            self.assertEqual(ambiguous.returncode, 4, ambiguous.stderr)
            self.assertIn("ambiguous-alias-edge", {item["code"] for item in report["findings"]})

    def test_all_scope_clis_reject_symlink_in_root_ancestor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            real = base / "real"
            scope = real / "scope"
            scope.mkdir(parents=True)
            document = scope / "rules.md"
            document.write_text("# Rules\n", encoding="utf-8")
            lexical = base / "lexical"
            lexical.mkdir()
            alias = lexical / "alias"
            try:
                alias.symlink_to(real, target_is_directory=True)
            except OSError:
                self.skipTest("Символические ссылки недоступны в окружении")
            symlink_root = alias / "scope"

            manifest = subprocess.run(
                [
                    sys.executable, str(CALCULATE), "--template", "--root", str(symlink_root),
                    "--purpose", "Check", "--version", "v1", "--unit", "rules.md", "core", "instruction",
                ],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(manifest.returncode, 2)
            scanned = subprocess.run(
                [sys.executable, str(SCAN), str(symlink_root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(scanned.returncode, 2)

            graph = self.graph(document, hashlib.sha256(document.read_bytes()).hexdigest())
            graph_path = base / "graph.json"
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            checked = subprocess.run(
                [sys.executable, str(GRAPH), str(graph_path), "--root", str(symlink_root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(checked.returncode, 2)

    def test_wrong_json_types_return_controlled_errors(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            document = root / "rules.md"
            document.write_text("# Rules\n", encoding="utf-8")
            graph = self.graph(document, hashlib.sha256(document.read_bytes()).hexdigest())
            graph["edges"][0]["type"] = []
            graph_path = root / "graph.json"
            graph_path.write_text(json.dumps(graph, ensure_ascii=False), encoding="utf-8")
            malformed_graph = subprocess.run(
                [sys.executable, str(GRAPH), str(graph_path), "--root", str(root)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(malformed_graph.returncode, 2)
            self.assertNotIn("Traceback", malformed_graph.stderr)

            review = self.valid_review()
            review["criteria"]["purpose_scope"]["status"] = []
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review, ensure_ascii=False), encoding="utf-8")
            malformed_review = subprocess.run(
                [sys.executable, str(CALCULATE), str(review_path)],
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(malformed_review.returncode, 2)
            self.assertNotIn("Traceback", malformed_review.stderr)

    def test_schema_contract_detects_all_protected_drift(self) -> None:
        graph_module = self.load_script_module("graph_contract_test", GRAPH)
        graph_schema_path = SCRIPT_DIR.parent / "references" / "instruction-graph.schema.json"
        graph_mutations = [
            lambda value: value["properties"]["mode"].update({"enum": ["explicit"]}),
            lambda value: value["properties"]["producer"].update({"enum": ["manual"]}),
            lambda value: value["properties"]["nodes"]["items"]["properties"]["claim"]["properties"]["modality"].update({"enum": ["MUST"]}),
            lambda value: value["properties"]["nodes"]["items"]["properties"]["claim"]["properties"]["polarity"].update({"enum": ["positive"]}),
            lambda value: value["properties"]["nodes"]["items"]["properties"]["id"].update({"pattern": ".*"}),
            lambda value: value["properties"]["edges"]["items"]["properties"]["id"].update({"pattern": ".*"}),
        ]
        pristine_graph_schema = json.loads(graph_schema_path.read_text(encoding="utf-8"))
        for mutate in graph_mutations:
            schema = json.loads(json.dumps(pristine_graph_schema))
            mutate(schema)
            with self.assertRaises(graph_module.GraphError):
                graph_module.validate_schema_contract(schema)

        calculate_module = self.load_script_module("review_contract_test", CALCULATE)
        review_schema_path = SCRIPT_DIR.parent / "references" / "review.schema.json"
        rubric_path = SCRIPT_DIR.parent / "references" / "rubric.json"
        pristine_review_schema = json.loads(review_schema_path.read_text(encoding="utf-8"))
        rubric = json.loads(rubric_path.read_text(encoding="utf-8"))
        review_mutations = [
            lambda value: value["$defs"]["systemicFinding"]["properties"]["id"].update({"pattern": ".*"}),
            lambda value: value["properties"]["criteria"].update({"minProperties": 1}),
            lambda value: value["$defs"]["evidence"]["properties"]["path"].update({"type": "array"}),
        ]
        for mutate in review_mutations:
            schema = json.loads(json.dumps(pristine_review_schema))
            mutate(schema)
            self.assertTrue(calculate_module.validate_schema_contract(schema, rubric))


if __name__ == "__main__":
    unittest.main()
