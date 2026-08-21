from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path
from urllib.parse import unquote


ROOT = Path(__file__).resolve().parents[1]
AUTHORITIES = ("AGENTS.md", "MODEL.md", "DOCS.md", "WORKFLOW.md")
MARKER_RE = re.compile(r"<!--\s*owner:([a-z0-9._-]+)\s*-->")
LINK_RE = re.compile(r"!?(?:\[[^\]]*\])\(([^)]+)\)")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s+#+)?\s*$")


def headings(text: str) -> list[tuple[int, str, int]]:
    result: list[tuple[int, str, int]] = []
    fenced = False
    for number, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith("```"):
            fenced = not fenced
            continue
        if fenced:
            continue
        match = HEADING_RE.match(line)
        if match:
            result.append((len(match.group(1)), match.group(2).strip(), number))
    return result


def split_target(raw: str) -> tuple[str, str]:
    target = raw.strip()
    if target.startswith("<") and ">" in target:
        target = target[1 : target.index(">")]
    else:
        target = target.split(maxsplit=1)[0]
    target = unquote(target)
    if "#" in target:
        return target.split("#", 1)
    return target, ""


def anchor_slug(title: str) -> str:
    title = re.sub(r"[`*_~]", "", title.casefold())
    title = re.sub(r"[^\w\-\s]", "", title, flags=re.UNICODE)
    return re.sub(r"[\s\-]+", "-", title).strip("-")


class InstructionChainTests(unittest.TestCase):
    def test_canonical_authorities_exist_and_have_valid_headings(self) -> None:
        for name in AUTHORITIES:
            path = ROOT / name
            self.assertTrue(path.is_file(), name)
            parsed = headings(path.read_text(encoding="utf-8"))
            self.assertEqual([item[0] for item in parsed].count(1), 1, name)
            previous = 0
            for level, _, line in parsed:
                if previous:
                    self.assertLessEqual(level, previous + 1, f"{name}:{line}")
                previous = level

    def test_agents_routes_to_all_peer_authorities(self) -> None:
        text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        links = {split_target(raw)[0] for raw in LINK_RE.findall(text)}
        self.assertEqual(set(AUTHORITIES) - {"AGENTS.md"}, links & (set(AUTHORITIES) - {"AGENTS.md"}))

    def test_relative_links_and_anchors_resolve(self) -> None:
        paths = [ROOT / name for name in (*AUTHORITIES, "README.md")]
        all_anchors = {
            path: {anchor_slug(title) for _, title, _ in headings(path.read_text(encoding="utf-8"))}
            for path in paths
        }
        for path in paths:
            for raw in LINK_RE.findall(path.read_text(encoding="utf-8")):
                target, anchor = split_target(raw)
                if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.IGNORECASE):
                    continue
                resolved = (path.parent / target).resolve()
                self.assertTrue(resolved.is_file(), f"{path.name}: {target}")
                if anchor and resolved in all_anchors:
                    self.assertIn(anchor.casefold(), {item.casefold() for item in all_anchors[resolved]})

    def test_active_docs_have_no_monorepo_or_broken_contract_routes(self) -> None:
        forbidden = ("/srv/bears", "BearsCLOUD/bears", "origin/dev", "bears-infra", "contracts/")
        for name in (*AUTHORITIES, "README.md"):
            text = (ROOT / name).read_text(encoding="utf-8")
            for value in forbidden:
                self.assertNotIn(value, text, f"{name}: {value}")

    def test_owner_markers_are_unique(self) -> None:
        owners: dict[str, str] = {}
        for name in AUTHORITIES:
            for owner in MARKER_RE.findall((ROOT / name).read_text(encoding="utf-8")):
                self.assertNotIn(owner, owners, f"duplicate owner marker: {owner}")
                owners[owner] = name
        self.assertEqual(set(owners.values()), set(AUTHORITIES))

    def test_readme_routes_the_instruction_chain(self) -> None:
        text = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in AUTHORITIES:
            self.assertRegex(text, rf"\]\({re.escape(name)}\)")

    def test_archived_agents_is_byte_exact_baseline(self) -> None:
        archive = ROOT / "legacy" / "instructions" / "2026-08-21" / "AGENTS.md"
        self.assertEqual(
            hashlib.sha256(archive.read_bytes()).hexdigest(),
            "9dbd6d6c57516438c8b48630c8144c5986439676c380734a28d7906fc9e50b2b",
        )


if __name__ == "__main__":
    unittest.main()
