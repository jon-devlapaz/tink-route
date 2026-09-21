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
    def test_install_flag_surfaces_references_and_scripts(self, mock_subprocess):
        import tempfile

        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "Added cro"
        mock_subprocess.return_value.stderr = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            cro_dir = tmppath / ".agents" / "skills" / "cro"
            (cro_dir / "references").mkdir(parents=True)
            (cro_dir / "scripts").mkdir(parents=True)
            (cro_dir / "SKILL.md").write_text("cro")
            (cro_dir / "references" / "form.md").write_text("form")
            (cro_dir / "references" / "experiments.md").write_text("experiments")
            (cro_dir / "scripts" / "audit.sh").write_text("#!/bin/sh")

            outcome = install_skill("cro", project_dir=tmppath)
            self.assertTrue(outcome["success"])
            self.assertEqual(outcome["skill_path"], ".agents/skills/cro/SKILL.md")
            self.assertEqual(outcome["references"], ["references/experiments.md", "references/form.md"])
            self.assertEqual(outcome["scripts"], ["scripts/audit.sh"])

    @patch("urllib.request.urlopen")
    def test_stage_2_uncertain_extracts_runner_up(self, mock_urlopen):
        resp_stage1 = MagicMock()
        resp_stage1.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "specialist_needed": {"type": "noul", "noul": 0.85}
            }
        }).encode("utf-8")

        resp_stage2 = MagicMock()
        resp_stage2.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "selected_skill": {
                    "type": "choice",
                    "choice": "cro",
                    "confidence": 0.55,
                    "probabilities": {"cro": 0.55, "landing-page": 0.40, "__no_skill__": 0.05}
                }
            }
        }).encode("utf-8")

        mock_urlopen.return_value.__enter__.side_effect = [resp_stage1, resp_stage2]

        client = JevRouterClient(api_key="test-key")
        result = client.route(
            task="Optimize marketing copy",
            skills=[
                {"name": "cro", "description": "Conversion rate optimization"},
                {"name": "landing-page", "description": "Landing page design"}
            ],
            threshold=0.60
        )

        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["top_candidate"], "cro")
        self.assertEqual(result["probability"], 0.55)
        self.assertEqual(result["runner_up"], "landing-page")
        self.assertEqual(result["runner_up_probability"], 0.40)
        self.assertEqual(result["margin"], 0.15)

    @patch("tink_route.cli.JevRouterClient.route")
    @patch("tink_route.cli.load_library_skills")
    def test_cli_exit_code_contract(self, mock_load, mock_route):
        from tink_route.cli import main
        import sys

        mock_load.return_value = [{"name": "fake", "description": "fake desc"}]

        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "dummy"}):
            # 1. Routed -> exit 0
            mock_route.return_value = {"status": "routed", "winner": "fake", "probability": 0.9, "confidence": 0.9, "specialist_noul": 0.9}
            with patch.object(sys, "argv", ["tink-route", "some task"]):
                self.assertEqual(main(), 0)

            # 2. No skill needed -> exit 1
            mock_route.return_value = {"status": "no_skill_needed", "task": "some task", "specialist_noul": 0.1, "threshold": 0.6}
            with patch.object(sys, "argv", ["tink-route", "some task"]):
                self.assertEqual(main(), 1)

            # 3. Uncertain -> exit 1
            mock_route.return_value = {"status": "uncertain", "task": "some task", "threshold": 0.6, "top_candidate": "fake", "probability": 0.5}
            with patch.object(sys, "argv", ["tink-route", "some task"]):
                self.assertEqual(main(), 1)

    def test_ephemeral_ledger_recording(self):
        import tempfile
        from tink_route.ephemeral import load_ephemeral_skills, record_ephemeral_skill

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            self.assertEqual(load_ephemeral_skills(tmppath), [])

            record_ephemeral_skill(tmppath, "threejs-shaders")
            self.assertEqual(load_ephemeral_skills(tmppath), ["threejs-shaders"])

            # Idempotent addition
            record_ephemeral_skill(tmppath, "threejs-shaders")
            self.assertEqual(load_ephemeral_skills(tmppath), ["threejs-shaders"])

            record_ephemeral_skill(tmppath, "cro")
            self.assertEqual(load_ephemeral_skills(tmppath), ["threejs-shaders", "cro"])

    @patch("subprocess.run")
    def test_prune_ephemeral_skills_with_manifest_protection(self, mock_subprocess):
        import tempfile
        from tink_route.ephemeral import prune_ephemeral_skills, record_ephemeral_skill

        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "Removed skill"
        mock_subprocess.return_value.stderr = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            skills_dir = tmppath / ".agents" / "skills"
            skills_dir.mkdir(parents=True)

            # 1. Ephemeral skill: threejs-shaders
            (skills_dir / "threejs-shaders").mkdir()
            (skills_dir / "threejs-shaders" / "SKILL.md").write_text("threejs")
            record_ephemeral_skill(tmppath, "threejs-shaders")

            # 2. Pinned permanent skill in skills.toml: my-permanent-skill
            (skills_dir / "my-permanent-skill").mkdir()
            (skills_dir / "my-permanent-skill" / "SKILL.md").write_text("permanent")
            tink_dir = tmppath / ".tink"
            tink_dir.mkdir(exist_ok=True)
            (tink_dir / "skills.toml").write_text('[[skills]]\nname = "my-permanent-skill"\n')

            # 3. Reserved skill: manage-tink
            (skills_dir / "manage-tink").mkdir()
            (skills_dir / "manage-tink" / "SKILL.md").write_text("manage-tink")

            # Test Dry Run first
            dry_res = prune_ephemeral_skills(tmppath, dry_run=True)
            self.assertEqual(dry_res["pruned"], ["threejs-shaders"])
            self.assertIn("manage-tink", dry_res["preserved"])
            self.assertIn("my-permanent-skill", dry_res["preserved"])
            mock_subprocess.assert_not_called()

            # Test Actual Pruning (ledger-only default preserves foreign skills)
            live_res = prune_ephemeral_skills(tmppath, dry_run=False)
            self.assertEqual(live_res["pruned"], ["threejs-shaders"])
            self.assertIn("manage-tink", live_res["preserved"])
            self.assertIn("my-permanent-skill", live_res["preserved"])
            mock_subprocess.assert_called_once_with(
                ["tink", "skill", "remove", "threejs-shaders"],
                cwd=str(tmppath),
                capture_output=True,
                text=True,
                check=False
            )

    @patch("subprocess.run")
    def test_prune_preserves_foreign_and_manual_skills(self, mock_subprocess):
        """Issue #1: Skills not recorded in ephemeral ledger must be preserved by default."""
        import tempfile
        from tink_route.ephemeral import prune_ephemeral_skills

        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "Removed skill"
        mock_subprocess.return_value.stderr = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            skills_dir = tmppath / ".agents" / "skills"
            skills_dir.mkdir(parents=True)

            # A foreign skill installed by Cursor or manual tink skill add (NOT in ephemeral.json)
            (skills_dir / "cursor-skill").mkdir()
            (skills_dir / "cursor-skill" / "SKILL.md").write_text("cursor")

            # Default prune (ledger-only): must preserve cursor-skill
            res_default = prune_ephemeral_skills(tmppath, dry_run=False, all_unpinned=False)
            self.assertEqual(res_default["pruned"], [])
            self.assertIn("cursor-skill", res_default["preserved"])
            mock_subprocess.assert_not_called()

            # Opt-in broad sweep: prunes cursor-skill
            res_sweep = prune_ephemeral_skills(tmppath, dry_run=False, all_unpinned=True)
            self.assertEqual(res_sweep["pruned"], ["cursor-skill"])
            mock_subprocess.assert_called_once_with(
                ["tink", "skill", "remove", "cursor-skill"],
                cwd=str(tmppath),
                capture_output=True,
                text=True,
                check=False
            )

    @patch("urllib.request.urlopen")
    def test_batched_routing_preserves_authentic_jev_confidence(self, mock_urlopen):
        """Issue #3: Multi-batch single winner must preserve authentic Jev confidence, not hardcoded 0.85."""
        # Stage 1: Specialist needed
        resp_stage1 = MagicMock()
        resp_stage1.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {"specialist_needed": {"type": "noul", "noul": 0.90}}
        }).encode("utf-8")

        # 25 dummy skills -> 2 batches (24 in batch 1, 1 in batch 2)
        skills = [{"name": f"skill-{i}", "description": f"desc {i}"} for i in range(25)]

        # Batch 1 returns winner skill-0 with authentic confidence 0.77 and prob 0.77
        resp_batch1 = MagicMock()
        resp_batch1.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "selected_skill": {
                    "type": "choice",
                    "choice": "skill-0",
                    "confidence": 0.77,
                    "probabilities": {"skill-0": 0.77, "__no_skill__": 0.23}
                }
            }
        }).encode("utf-8")

        # Batch 2 returns __no_skill__
        resp_batch2 = MagicMock()
        resp_batch2.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "selected_skill": {
                    "type": "choice",
                    "choice": "__no_skill__",
                    "confidence": 0.99,
                    "probabilities": {"__no_skill__": 0.99}
                }
            }
        }).encode("utf-8")

        mock_urlopen.return_value.__enter__.side_effect = [resp_stage1, resp_batch1, resp_batch2]

        client = JevRouterClient(api_key="test-key")
        result = client.route(task="Some task", skills=skills, threshold=0.60)

        self.assertEqual(result["status"], "routed")
        self.assertEqual(result["winner"], "skill-0")
        # Must be authentic 0.77, NOT hardcoded 0.85
        self.assertEqual(result["confidence"], 0.77)
        self.assertEqual(result["probability"], 0.77)

    @patch("tink_route.cli.install_skill")
    @patch("tink_route.cli.JevRouterClient.route")
    @patch("tink_route.cli.load_library_skills")
    def test_activation_contract_in_json_output(self, mock_load, mock_route, mock_install):
        """Issue #2 & Critique P1.3: JSON output activation contract for install vs recommendation-only."""
        import sys
        import io
        from tink_route.cli import main

        mock_load.return_value = [{"name": "fake", "description": "fake desc"}]
        mock_route.return_value = {
            "status": "routed",
            "winner": "fake",
            "probability": 0.9,
            "confidence": 0.9,
            "specialist_noul": 0.9,
            "threshold": 0.6
        }
        mock_install.return_value = {
            "success": True,
            "skill_path": ".agents/skills/fake/SKILL.md",
            "references": [],
            "scripts": [],
            "stdout": "Installed",
            "stderr": "",
            "code": 0,
            "was_pre_existing": False,
        }

        # 1. Recommendation-only: mode is install_required, entrypoint is None
        captured_rec = io.StringIO()
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "dummy"}):
            with patch.object(sys, "argv", ["tink-route", "--json", "some task"]):
                with patch("sys.stdout", captured_rec):
                    code = main()

        self.assertEqual(code, 0)
        rec_data = json.loads(captured_rec.getvalue())
        self.assertIn("activation", rec_data)
        self.assertEqual(rec_data["activation"]["mode"], "install_required")
        self.assertIsNone(rec_data["activation"]["entrypoint"])

        # 2. Installed with -i: mode is direct_read, entrypoint is populated
        captured_inst = io.StringIO()
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "dummy"}):
            with patch.object(sys, "argv", ["tink-route", "--json", "-i", "some task"]):
                with patch("sys.stdout", captured_inst):
                    code_inst = main()

        self.assertEqual(code_inst, 0)
        inst_data = json.loads(captured_inst.getvalue())
        self.assertIn("activation", inst_data)
        self.assertEqual(inst_data["activation"]["mode"], "direct_read")
        self.assertEqual(inst_data["activation"]["entrypoint"], ".agents/skills/fake/SKILL.md")
        self.assertFalse(inst_data["activation"]["restart_required"])

    @patch("subprocess.run")
    def test_pre_existing_manual_install_not_adopted(self, mock_subprocess):
        """Critique P1.1: Re-installing a pre-existing manual skill must not adopt it into ephemeral ledger."""
        import tempfile
        import sys
        from tink_route.cli import main
        from tink_route.ephemeral import load_ephemeral_skills, prune_ephemeral_skills

        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "Unchanged cro"
        mock_subprocess.return_value.stderr = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            skills_dir = tmppath / ".agents" / "skills" / "cro"
            skills_dir.mkdir(parents=True)
            (skills_dir / "SKILL.md").write_text("pre-existing manual cro")

            with patch("tink_route.cli.load_library_skills", return_value=[{"name": "cro", "description": "CRO"}]), \
                 patch("tink_route.cli.JevRouterClient.route", return_value={"status": "routed", "winner": "cro", "probability": 0.95, "confidence": 0.95, "specialist_noul": 0.9}):
                with patch.dict("os.environ", {"TYPESAFE_API_KEY": "dummy"}), \
                     patch("pathlib.Path.cwd", return_value=tmppath):
                    with patch.object(sys, "argv", ["tink-route", "-i", "Optimize CRO"]):
                        code = main()

            self.assertEqual(code, 0)
            # Ephemeral ledger must NOT adopt pre-existing cro!
            self.assertEqual(load_ephemeral_skills(tmppath), [])

            # Prune must preserve cro
            prune_res = prune_ephemeral_skills(tmppath)
            self.assertEqual(prune_res["pruned"], [])
            self.assertIn("cro", prune_res["preserved"])

    def test_toml_manifest_pinning_and_fail_closed(self):
        """Critique P1.2: tomllib handles valid TOML syntax and fails closed on invalid TOML."""
        import tempfile
        from tink_route.ephemeral import load_pinned_skills, ManifestSyntaxError

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            tink_dir = tmppath / ".tink"
            tink_dir.mkdir()
            manifest = tink_dir / "skills.toml"

            # Quoted keys in array of tables
            manifest.write_text('[[skills]]\n"name" = "cro"\n')
            pinned = load_pinned_skills(tmppath)
            self.assertIn("cro", pinned)

            # Key-value table
            manifest.write_text('[skills.landing-page]\nversion = "1.0"\n')
            pinned = load_pinned_skills(tmppath)
            self.assertIn("landing-page", pinned)

            # Malformed TOML must raise ManifestSyntaxError (fail-closed)
            manifest.write_text('[[skills\ninvalid toml content')
            with self.assertRaises(ManifestSyntaxError):
                load_pinned_skills(tmppath)

    @patch("tink_route.cli.install_skill")
    @patch("tink_route.cli.JevRouterClient.route")
    @patch("tink_route.cli.load_library_skills")
    def test_failed_installation_exits_2(self, mock_load, mock_route, mock_install):
        """Critique P1.3: Failed installation exits 2 and suppresses direct_read activation."""
        import sys
        import io
        from tink_route.cli import main

        mock_load.return_value = [{"name": "cro", "description": "CRO"}]
        mock_route.return_value = {
            "status": "routed",
            "winner": "cro",
            "probability": 0.9,
            "confidence": 0.9,
            "specialist_noul": 0.9,
            "threshold": 0.6
        }
        mock_install.return_value = {
            "success": False,
            "skill_path": None,
            "references": [],
            "scripts": [],
            "stdout": "",
            "stderr": "Library skill not found",
            "code": 1,
            "was_pre_existing": False,
        }

        captured = io.StringIO()
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "dummy"}):
            with patch.object(sys, "argv", ["tink-route", "--json", "-i", "Optimize CRO"]):
                with patch("sys.stdout", captured):
                    code = main()

        self.assertEqual(code, 2)
        data = json.loads(captured.getvalue())
        self.assertFalse(data.get("installed", True))
        self.assertNotIn("activation", data)

    def test_malformed_ephemeral_ledger_hygiene(self):
        """Critique P2.1: Malformed ephemeral.json does not raise TypeError."""
        import tempfile
        from tink_route.ephemeral import load_ephemeral_skills, prune_ephemeral_skills

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            tink_dir = tmppath / ".tink"
            tink_dir.mkdir()
            ledger = tink_dir / "ephemeral.json"

            # null skills list
            ledger.write_text('{"skills": null}')
            self.assertEqual(load_ephemeral_skills(tmppath), [])
            # Prune should not raise
            res = prune_ephemeral_skills(tmppath)
            self.assertEqual(res["pruned"], [])

            # unhashable elements in skills
            ledger.write_text('{"skills": [{}]}')
            self.assertEqual(load_ephemeral_skills(tmppath), [])

    @patch("urllib.request.urlopen")
    def test_batched_routing_preserves_no_match(self, mock_urlopen):
        """Critique P2.2: Multi-batch where all batches return __no_match__ reports status 'no_match'."""
        # Stage 1: Specialist needed (0.92)
        resp_stage1 = MagicMock()
        resp_stage1.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {"specialist_needed": {"type": "noul", "noul": 0.92}}
        }).encode("utf-8")

        # 25 dummy skills -> 2 batches
        skills = [{"name": f"skill-{i}", "description": f"desc {i}"} for i in range(25)]

        # Batch 1 returns __no_match__
        resp_batch1 = MagicMock()
        resp_batch1.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "selected_skill": {
                    "type": "choice",
                    "choice": "__no_match__",
                    "confidence": 0.95,
                    "probabilities": {"__no_match__": 0.95}
                }
            }
        }).encode("utf-8")

        # Batch 2 returns __no_match__
        resp_batch2 = MagicMock()
        resp_batch2.read.return_value = json.dumps({
            "model": "jev-1.13.0",
            "answers": {
                "selected_skill": {
                    "type": "choice",
                    "choice": "__no_match__",
                    "confidence": 0.93,
                    "probabilities": {"__no_match__": 0.93}
                }
            }
        }).encode("utf-8")

        mock_urlopen.return_value.__enter__.side_effect = [resp_stage1, resp_batch1, resp_batch2]

        client = JevRouterClient(api_key="test-key")
        result = client.route(task="Some task", skills=skills, threshold=0.60)

        # Must report no_match, NOT no_skill_needed!
        self.assertEqual(result["status"], "no_match")
        self.assertEqual(result["specialist_noul"], 0.92)
        self.assertGreaterEqual(result["confidence"], 0.90)

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

    def test_prune_dry_run_exits_zero(self):
        """Critique P2.5: prune --dry-run exits 0 even if count is 0."""
        import tempfile
        import sys
        from tink_route.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            with patch("pathlib.Path.cwd", return_value=tmppath):
                with patch.object(sys, "argv", ["tink-route", "prune", "--dry-run"]):
                    code = main()

        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
