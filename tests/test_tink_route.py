import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tink_route.adapters.client import JevRouterClient
from tink_route.cli import install_skill
from tink_route.core.models import InstallOutcome, PruneReport, RoutingResult
from tink_route.metadata import load_library_skills, parse_skill_metadata


class TestTinkRoute(unittest.TestCase):

    def setUp(self):
        import tempfile
        self._lib_tmp = tempfile.TemporaryDirectory(prefix="tink-lib-")
        self._lib_patch = patch("tink_route.cli.DEFAULT_LIBRARY_PATH", Path(self._lib_tmp.name))
        self._lib_patch.start()
        self.addCleanup(self._lib_patch.stop)
        self.addCleanup(self._lib_tmp.cleanup)

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
            self.assertTrue(outcome.success)
            self.assertEqual(outcome.skill_path, ".agents/skills/cro/SKILL.md")
            self.assertEqual(outcome.references, ["references/experiments.md", "references/form.md"])
            self.assertEqual(outcome.scripts, ["scripts/audit.sh"])

    def test_ephemeral_ledger_recording(self):
        import tempfile
        from tink_route import load_ephemeral_skills, record_ephemeral_skill

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
        from tink_route import prune_ephemeral_skills, record_ephemeral_skill

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
            self.assertEqual(dry_res.pruned, ["threejs-shaders"])
            self.assertIn("manage-tink", dry_res.preserved)
            self.assertIn("my-permanent-skill", dry_res.preserved)
            mock_subprocess.assert_not_called()

            # Test Actual Pruning (ledger-only default preserves foreign skills)
            live_res = prune_ephemeral_skills(tmppath, dry_run=False)
            self.assertEqual(live_res.pruned, ["threejs-shaders"])
            self.assertIn("manage-tink", live_res.preserved)
            self.assertIn("my-permanent-skill", live_res.preserved)
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
        from tink_route import prune_ephemeral_skills

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
            self.assertEqual(res_default.pruned, [])
            self.assertIn("cursor-skill", res_default.preserved)
            mock_subprocess.assert_not_called()

            # Opt-in broad sweep: prunes cursor-skill
            res_sweep = prune_ephemeral_skills(tmppath, dry_run=False, all_unpinned=True)
            self.assertEqual(res_sweep.pruned, ["cursor-skill"])
            mock_subprocess.assert_called_once_with(
                ["tink", "skill", "remove", "cursor-skill"],
                cwd=str(tmppath),
                capture_output=True,
                text=True,
                check=False
            )

    @patch("tink_route.core.engine.RoutingEngine.install_skill_locked")
    @patch("tink_route.cli.JevRouterClient.route")
    @patch("tink_route.cli.load_library_skills")
    def test_activation_contract_in_json_output(self, mock_load, mock_route, mock_install):
        """Issue #2 & Critique P1.3: JSON output activation contract for install vs recommendation-only."""
        import sys
        import io
        from tink_route.cli import main

        mock_load.return_value = [{"name": "fake", "description": "fake desc"}]
        mock_route.return_value = RoutingResult(
            status="routed",
            task="some task",
            winner="fake",
            probability=0.9,
            confidence=0.9,
            specialist_noul=0.9,
            threshold=0.6,
        )
        mock_install.return_value = InstallOutcome(
            success=True,
            stdout="Installed",
            stderr="",
            code=0,
            skill_path=".agents/skills/fake/SKILL.md",
            was_pre_existing=False,
        )

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
        from tink_route import load_ephemeral_skills, prune_ephemeral_skills

        mock_subprocess.return_value.returncode = 0
        mock_subprocess.return_value.stdout = "Unchanged cro"
        mock_subprocess.return_value.stderr = ""

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            skills_dir = tmppath / ".agents" / "skills" / "cro"
            skills_dir.mkdir(parents=True)
            (skills_dir / "SKILL.md").write_text("pre-existing manual cro")

            with patch("tink_route.cli.load_library_skills", return_value=[{"name": "cro", "description": "CRO"}]), \
                 patch("tink_route.cli.JevRouterClient.route", return_value=RoutingResult(status="routed", task="Optimize CRO", winner="cro", probability=0.95, confidence=0.95, specialist_noul=0.9)):
                with patch.dict("os.environ", {"TYPESAFE_API_KEY": "dummy"}), \
                     patch("pathlib.Path.cwd", return_value=tmppath):
                    with patch.object(sys, "argv", ["tink-route", "-i", "Optimize CRO"]):
                        code = main()

            self.assertEqual(code, 0)
            # Ephemeral ledger must NOT adopt pre-existing cro!
            self.assertEqual(load_ephemeral_skills(tmppath), [])

            # Prune must preserve cro
            prune_res = prune_ephemeral_skills(tmppath)
            self.assertEqual(prune_res.pruned, [])
            self.assertIn("cro", prune_res.preserved)

    def test_toml_manifest_pinning_and_fail_closed(self):
        """Critique P1.2: tomllib handles valid TOML syntax and fails closed on invalid TOML."""
        import tempfile
        from tink_route import ManifestSyntaxError
        from tink_route.adapters.ledger import default_ledger

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            tink_dir = tmppath / ".tink"
            tink_dir.mkdir()
            manifest = tink_dir / "skills.toml"

            # Quoted keys in array of tables
            manifest.write_text('[[skills]]\n"name" = "cro"\n')
            pinned = default_ledger.load_pinned_skills(tmppath)
            self.assertIn("cro", pinned)

            # Key-value table
            manifest.write_text('[skills.landing-page]\nversion = "1.0"\n')
            pinned = default_ledger.load_pinned_skills(tmppath)
            self.assertIn("landing-page", pinned)

            # Malformed TOML must raise ManifestSyntaxError (fail-closed)
            manifest.write_text('[[skills\ninvalid toml content')
            with self.assertRaises(ManifestSyntaxError):
                default_ledger.load_pinned_skills(tmppath)

    @patch("tink_route.core.engine.RoutingEngine.install_skill_locked")
    @patch("tink_route.cli.JevRouterClient.route")
    @patch("tink_route.cli.load_library_skills")
    def test_failed_installation_exits_2(self, mock_load, mock_route, mock_install):
        """Critique P1.3: Failed installation exits 2 and suppresses direct_read activation."""
        import sys
        import io
        from tink_route.cli import main

        mock_load.return_value = [{"name": "cro", "description": "CRO"}]
        mock_route.return_value = RoutingResult(
            status="routed",
            task="Optimize CRO",
            winner="cro",
            probability=0.9,
            confidence=0.9,
            specialist_noul=0.9,
            threshold=0.6,
        )
        mock_install.return_value = InstallOutcome(
            success=False,
            stdout="",
            stderr="Library skill not found",
            code=1,
            skill_path=None,
            was_pre_existing=False,
        )

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
        from tink_route import load_ephemeral_skills, prune_ephemeral_skills

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
            self.assertEqual(res.pruned, [])

            # unhashable elements in skills
            ledger.write_text('{"skills": [{}]}')
            self.assertEqual(load_ephemeral_skills(tmppath), [])

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

    def test_concurrent_ledger_writes_no_loss(self):
        """Audit Finding 1: Concurrent writes to ephemeral.json must not lose records."""
        import tempfile
        import threading
        from tink_route import load_ephemeral_skills, record_ephemeral_skill

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            num_threads = 12
            barrier = threading.Barrier(num_threads)

            def worker(idx):
                barrier.wait()
                record_ephemeral_skill(tmppath, f"skill-{idx}")

            threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            recorded = set(load_ephemeral_skills(tmppath))
            expected = {f"skill-{i}" for i in range(num_threads)}
            self.assertEqual(recorded, expected)

    @patch("tink_route.core.engine.RoutingEngine.prune")
    def test_partial_prune_failure_returns_exit_2(self, mock_prune):
        """Audit Finding 2: Partial prune failures must return exit code 2."""
        import sys
        from tink_route.cli import main

        mock_prune.return_value = PruneReport(
            pruned=["success-skill"],
            preserved=[],
            errors=[{"skill": "failing-skill", "error": "removal failed"}],
            dry_run=False,
            count=1,
        )
        with patch.object(sys, "argv", ["tink-route", "prune"]):
            code = main()

        self.assertEqual(code, 2)

    @patch("tink_route.adapters.ledger.FilesystemLedger.record_ephemeral_skill_locked")
    @patch("tink_route.core.engine.RoutingEngine.install_skill_locked")
    @patch("tink_route.cli.JevRouterClient.route")
    @patch("tink_route.cli.load_library_skills")
    def test_ledger_write_failure_handled_cleanly(self, mock_load, mock_route, mock_install, mock_record):
        """Audit Finding 3: Ledger write failures must not escape as unhandled exceptions."""
        import sys
        import io
        from tink_route.cli import main

        mock_load.return_value = [{"name": "cro", "description": "CRO"}]
        mock_route.return_value = RoutingResult(
            status="routed",
            task="Optimize CRO",
            winner="cro",
            probability=0.95,
            confidence=0.95,
            specialist_noul=0.9,
            threshold=0.6,
        )
        mock_install.return_value = InstallOutcome(
            success=True,
            stdout="Installed",
            stderr="",
            code=0,
            skill_path=".agents/skills/cro/SKILL.md",
            was_pre_existing=False,
        )
        mock_record.side_effect = PermissionError("Read-only filesystem")

        captured = io.StringIO()
        with patch.dict("os.environ", {"TYPESAFE_API_KEY": "dummy"}):
            with patch.object(sys, "argv", ["tink-route", "--json", "-i", "Optimize CRO"]):
                with patch("sys.stdout", captured):
                    code = main()

        self.assertEqual(code, 2)
        data = json.loads(captured.getvalue())
        self.assertTrue(data.get("installed"))
        self.assertIn("Failed to record ephemeral ledger", data.get("tracking_error", ""))
        self.assertNotIn("activation", data)

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


if __name__ == "__main__":
    unittest.main()
