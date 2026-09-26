"""Tests for skillset resolution and stage-constrained routing."""

import json
import tempfile
import unittest
from pathlib import Path

from tink_route.core.constants import STAGE_TO_SKILLSET, get_default_tink_home
from tink_route.core.exceptions import SkillsetError
from tink_route.metadata import resolve_skillset_members
from tink_route.cli import build_parser


class TestResolveSkillsetMembers(unittest.TestCase):
    def test_resolve_skillset_members_from_pin(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            skillsets_dir = tmp_path / "skillsets"
            skillsets_dir.mkdir()
            pin = skillsets_dir / "testing-skillset.json"
            pin.write_text(json.dumps({"members": ["control-cli", "control-ui", "tdd"]}))

            members = resolve_skillset_members("testing-skillset", tink_home=tmp_path)
            self.assertEqual(members, {"control-cli", "control-ui", "tdd"})

            # Bare name without suffix
            members_bare = resolve_skillset_members("testing", tink_home=tmp_path)
            self.assertEqual(members_bare, {"control-cli", "control-ui", "tdd"})

    def test_resolve_skillset_members_missing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            skillsets_dir = tmp_path / "skillsets"
            skillsets_dir.mkdir()
            with self.assertRaisesRegex(SkillsetError, "not found"):
                resolve_skillset_members("nonexistent-skillset", tink_home=tmp_path)

    def test_resolve_skillset_members_invalid_name(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            with self.assertRaisesRegex(SkillsetError, "Invalid skillset name"):
                resolve_skillset_members("../evil/path", tink_home=tmp_path)

    def test_resolve_skillset_members_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            skillsets_dir = tmp_path / "skillsets"
            skillsets_dir.mkdir()
            pin = skillsets_dir / "broken-skillset.json"
            pin.write_text("invalid json")
            with self.assertRaisesRegex(SkillsetError, "Malformed skillset file"):
                resolve_skillset_members("broken-skillset", tink_home=tmp_path)


class TestStageMappings(unittest.TestCase):
    def test_resolve_stage_mappings(self):
        self.assertEqual(STAGE_TO_SKILLSET["plan"], "planning-skillset")
        self.assertEqual(STAGE_TO_SKILLSET["01-plan"], "planning-skillset")
        self.assertEqual(STAGE_TO_SKILLSET["test"], "testing-skillset")
        self.assertEqual(STAGE_TO_SKILLSET["04-test"], "testing-skillset")
        self.assertEqual(STAGE_TO_SKILLSET["build"], "build-skillset")
        self.assertEqual(STAGE_TO_SKILLSET["03-build"], "build-skillset")

    def test_pstack_stage_skillsets_live(self):
        """Verify that all 6 stage skillsets in ~/.tink-library/skillsets resolve with pstack skills."""
        tink_home = get_default_tink_home()
        if not (tink_home / "skillsets").is_dir():
            self.skipTest("local pstack skillsets not installed")

        plan_members = resolve_skillset_members("planning-skillset", tink_home=tink_home)
        self.assertIn("why", plan_members)
        self.assertIn("how", plan_members)

        design_members = resolve_skillset_members("design-skillset", tink_home=tink_home)
        self.assertIn("architect", design_members)
        self.assertIn("blast-radius", design_members)

        build_members = resolve_skillset_members("build-skillset", tink_home=tink_home)
        self.assertIn("tdd", build_members)
        self.assertIn("deslop", build_members)

        test_members = resolve_skillset_members("testing-skillset", tink_home=tink_home)
        self.assertIn("control-cli", test_members)
        self.assertIn("control-ui", test_members)
        self.assertIn("create-verification-skill", test_members)

        deploy_members = resolve_skillset_members("deployment-skillset", tink_home=tink_home)
        self.assertIn("interrogate", deploy_members)

        maintain_members = resolve_skillset_members("maintenance-skillset", tink_home=tink_home)
        self.assertIn("principle-fix-root-causes", maintain_members)


class TestCliParserSkillsetOptions(unittest.TestCase):
    def test_cli_parser_skillset_options(self):
        parser = build_parser()
        args = parser.parse_args(["--skillset", "testing-skillset", "my task"])
        self.assertEqual(args.skillset, "testing-skillset")

        args_stage = parser.parse_args(["--stage", "test", "my task"])
        self.assertEqual(args_stage.stage, "test")


if __name__ == "__main__":
    unittest.main()
