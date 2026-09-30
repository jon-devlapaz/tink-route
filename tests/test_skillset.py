"""Tests for skillset resolution and stage-constrained routing."""

import json
import tempfile
import unittest
from pathlib import Path

from tink_route.core.constants import get_default_tink_home
from tink_route.core.exceptions import SkillsetError
from tink_route.metadata import resolve_skillset
from tink_route.cli import build_parser


def member_names(*args, **kwargs):
    return resolve_skillset(*args, **kwargs)[0]


class TestResolveSkillset(unittest.TestCase):
    def test_resolve_skillset_from_pin(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            skillsets_dir = tmp_path / "skillsets"
            skillsets_dir.mkdir()
            pin = skillsets_dir / "testing-skillset.json"
            pin.write_text(json.dumps({"members": ["control-cli", "control-ui", "tdd"]}))

            found = member_names("testing-skillset", tink_home=tmp_path)
            self.assertEqual(found, {"control-cli", "control-ui", "tdd"})

            # Bare name without suffix
            found_bare = member_names("testing", tink_home=tmp_path)
            self.assertEqual(found_bare, {"control-cli", "control-ui", "tdd"})

    def test_resolve_skillset_missing(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            skillsets_dir = tmp_path / "skillsets"
            skillsets_dir.mkdir()
            with self.assertRaisesRegex(SkillsetError, "not found"):
                member_names("nonexistent-skillset", tink_home=tmp_path)

    def test_resolve_skillset_invalid_name(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            with self.assertRaisesRegex(SkillsetError, "Invalid skillset name"):
                member_names("../evil/path", tink_home=tmp_path)

    def test_resolve_skillset_malformed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp_path = Path(td)
            skillsets_dir = tmp_path / "skillsets"
            skillsets_dir.mkdir()
            pin = skillsets_dir / "broken-skillset.json"
            pin.write_text("invalid json")
            with self.assertRaisesRegex(SkillsetError, "Malformed skillset file"):
                member_names("broken-skillset", tink_home=tmp_path)


class TestLiveStageSkillsets(unittest.TestCase):
    def test_pstack_stage_skillsets_live(self):
        """Verify that all 6 stage skillsets in ~/.tink-library/skillsets resolve with pstack skills."""
        tink_home = get_default_tink_home()
        if not (tink_home / "skillsets").is_dir():
            self.skipTest("local pstack skillsets not installed")

        plan_members = member_names("planning-skillset", tink_home=tink_home)
        self.assertIn("why", plan_members)
        self.assertIn("how", plan_members)

        design_members = member_names("design-skillset", tink_home=tink_home)
        self.assertIn("architect", design_members)
        self.assertIn("blast-radius", design_members)

        build_members = member_names("build-skillset", tink_home=tink_home)
        self.assertIn("tdd", build_members)
        self.assertIn("deslop", build_members)

        test_members = member_names("testing-skillset", tink_home=tink_home)
        self.assertIn("control-cli", test_members)
        self.assertIn("control-ui", test_members)
        self.assertIn("create-verification-skill", test_members)

        deploy_members = member_names("deployment-skillset", tink_home=tink_home)
        self.assertIn("interrogate", deploy_members)

        maintain_members = member_names("maintenance-skillset", tink_home=tink_home)
        self.assertIn("principle-fix-root-causes", maintain_members)


class TestCliParserSkillsetOptions(unittest.TestCase):
    def test_cli_parser_skillset_options(self):
        args = build_parser().parse_args(["--skillset", "testing-skillset", "my task"])
        self.assertEqual(args.skillset, "testing-skillset")
        self.assertFalse(args.anywhere)
        self.assertTrue(build_parser().parse_args(["--anywhere", "my task"]).anywhere)


if __name__ == "__main__":
    unittest.main()
