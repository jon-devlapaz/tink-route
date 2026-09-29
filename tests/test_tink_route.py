import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tink_route.adapters.client import JevRouterClient
from tink_route.metadata import load_library_skills, parse_skill_metadata


class TestTinkRoute(unittest.TestCase):
    def test_parse_frontmatter(self):
        raw = """---
name: test-skill
description: A skill for testing.
---
# Test Skill Body
"""
        meta = parse_skill_metadata(raw, "fallback-name")
        self.assertEqual(meta["name"], "test-skill")
        self.assertEqual(meta["description"], "A skill for testing.")

    def test_parse_frontmatter_fallback_name(self):
        raw = """---
description: A skill without explicit name.
---
"""
        meta = parse_skill_metadata(raw, "my-dir-name")
        self.assertEqual(meta["name"], "my-dir-name")
        self.assertEqual(meta["description"], "A skill without explicit name.")

    def test_load_library_skills(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            skill_a = tmppath / "skill-a"
            skill_a.mkdir()
            (skill_a / "SKILL.md").write_text("---\nname: skill-a\ndescription: Skill A description\n---\n")

            skill_b = tmppath / "skill-b"
            skill_b.mkdir()
            (skill_b / "SKILL.md").write_text("---\ndescription: Skill B description\n---\n")

            skills = load_library_skills(tmppath)
            self.assertEqual(len(skills), 2)
            names = [s["name"] for s in skills]
            self.assertIn("skill-a", names)
            self.assertIn("skill-b", names)

    def test_frontmatter_strips_quotes(self):
        """Critique P2.3: parse_skill_metadata strips surrounding quotes from name and description."""
        raw = """---
name: "quoted-skill"
description: 'quoted description'
---
"""
        meta = parse_skill_metadata(raw, "fallback")
        self.assertEqual(meta["name"], "quoted-skill")
        self.assertEqual(meta["description"], "quoted description")

    @patch("urllib.request.urlopen")
    def test_unknown_and_path_traversal_api_choice_rejected(self, mock_urlopen):
        """Audit Finding 4: Unknown or path-traversal candidate from API must be rejected."""
        resp_stage1 = MagicMock()
        resp_stage1.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {"specialised_workflow": {"type": "noul", "noul": 0.90}}
        }).encode("utf-8")

        # API tries to return ../outside
        resp_stage2 = MagicMock()
        resp_stage2.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "selected_skill": {
                    "type": "choice",
                    "choice": "../outside",
                    "confidence": 0.99,
                    "probabilities": {"../outside": 0.99}
                }
            }
        }).encode("utf-8")

        mock_urlopen.return_value.__enter__.side_effect = [resp_stage1, resp_stage2]

        client = JevRouterClient(api_key="test-key")
        with self.assertRaises(RuntimeError) as ctx:
            client.route(
                task="Malicious task",
                skills=[{"name": "valid-skill", "description": "Valid skill"}],
                threshold=0.60
            , tri_gate=False, rerank=False)

        self.assertIn("invalid candidate", str(ctx.exception))


class TestDefaultLibraryPath(unittest.TestCase):

    def _fresh_path(self):
        import tink_route.core.constants as constants
        return constants.get_default_library_path()

    def test_tink_home_absolute_takes_precedence(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.dict("os.environ", {"TINK_HOME": tmpdir}):
                self.assertEqual(self._fresh_path(), Path(tmpdir) / "skills")

    def test_tink_home_relative_absolutized_against_cwd(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            cwd = Path(tmpdir)
            with patch("pathlib.Path.cwd", return_value=cwd):
                with patch.dict("os.environ", {"TINK_HOME": "rel-home"}):
                    self.assertEqual(self._fresh_path(), cwd / "rel-home" / "skills")

    def test_canonical_default_when_no_tink_home(self):
        import tempfile
        with tempfile.TemporaryDirectory() as fake_home:
            with patch("pathlib.Path.home", return_value=Path(fake_home)):
                with patch.dict("os.environ", {}, clear=False):
                    import os
                    os.environ.pop("TINK_HOME", None)
                    self.assertEqual(
                        self._fresh_path(),
                        Path(fake_home) / ".tink-library" / "skills",
                    )


if __name__ == "__main__":
    unittest.main()
