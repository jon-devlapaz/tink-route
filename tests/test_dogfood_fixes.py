"""Regression tests for dogfood findings (subagents sa-1/sa-2/sa-3), Jev-prioritized."""

import tempfile
import unittest
from pathlib import Path

from tink_route.metadata import load_library_skills, parse_skill_metadata


class TestLibraryRobustness(unittest.TestCase):
    def test_malformed_skill_skipped_good_skill_loads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td)
            (lib / "good").mkdir()
            (lib / "good" / "SKILL.md").write_text("---\nname: good\ndescription: fine\n---\nbody")
            (lib / "badname").mkdir()
            (lib / "badname" / "SKILL.md").write_text("---\nname: bad name\ndescription: x\n---\n")
            skills = load_library_skills(lib)
        self.assertEqual([s["name"] for s in skills], ["good"])

    def test_unclosed_frontmatter_treated_as_body(self) -> None:
        meta = parse_skill_metadata("---\nname: foo\ndescription: bar\nno closing", "fallback")
        self.assertEqual(meta["description"], "")
        self.assertIn("name: foo", meta.get("body", ""))

    def test_non_utf8_bytes_do_not_crash_loader(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td)
            (lib / "weird").mkdir()
            (lib / "weird" / "SKILL.md").write_bytes(b"---\nname: weird\ndescription: hi \xff\xfe bad\n---\n")
            skills = load_library_skills(lib)
        self.assertEqual(len(skills), 1)
