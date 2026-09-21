import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tink_route.client import JevRouterClient
from tink_route.cli import install_skill
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
        skills = load_library_skills(Path.home() / ".tink" / "skills")
        self.assertGreater(len(skills), 10)
        names = [s["name"] for s in skills]
        self.assertIn("manage-tink", names)

    @patch("urllib.request.urlopen")
    def test_stage_1_no_skill_needed(self, mock_urlopen):
        mock_response = MagicMock()
        mock_response.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "specialist_needed": {"type": "noul", "noul": 0.15}
            }
        }).encode("utf-8")
        mock_urlopen.return_value.__enter__.return_value = mock_response

        client = JevRouterClient(api_key="test-key")
        result = client.route(
            task="Fix typo in variable name",
            skills=[{"name": "code-review", "description": "Reviews code"}],
            threshold=0.60
        )

        self.assertEqual(result["status"], "no_skill_needed")
        self.assertLess(result["specialist_noul"], 0.60)
        self.assertEqual(mock_urlopen.call_count, 1)

    @patch("urllib.request.urlopen")
    def test_stage_2_routes_winner(self, mock_urlopen):
        resp_stage1 = MagicMock()
        resp_stage1.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "specialist_needed": {"type": "noul", "noul": 0.92}
            }
        }).encode("utf-8")

        resp_stage2 = MagicMock()
        resp_stage2.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "selected_skill": {
                    "type": "choice",
                    "choice": "threejs-shaders",
                    "confidence": 0.95,
                    "probabilities": {"threejs-shaders": 0.95, "other": 0.05}
                }
            }
        }).encode("utf-8")

        mock_urlopen.return_value.__enter__.side_effect = [resp_stage1, resp_stage2]

        client = JevRouterClient(api_key="test-key")
        result = client.route(
            task="Build a custom fragment shader in GLSL",
            skills=[
                {"name": "threejs-shaders", "description": "GLSL and shaders"},
                {"name": "code-review", "description": "Reviews code"}
            ],
            threshold=0.60
        )

        self.assertEqual(result["status"], "routed")
        self.assertEqual(result["winner"], "threejs-shaders")
        self.assertGreaterEqual(result["probability"], 0.60)
        self.assertEqual(mock_urlopen.call_count, 2)

    @patch("subprocess.run")
    def test_install_flag_executes_tink(self, mock_subprocess):
        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "Added threejs-shaders"
        mock_subprocess.return_value.stderr = ""

        outcome = install_skill("threejs-shaders")
        self.assertTrue(outcome["success"])
        mock_subprocess.assert_called_once_with(
            ["tink", "skill", "add", "threejs-shaders"],
            capture_output=True,
            text=True,
            check=False
        )


if __name__ == "__main__":
    unittest.main()
