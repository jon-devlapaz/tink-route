"""Tests for skillset resolution and stage-constrained routing."""

import json
from pathlib import Path
import pytest

from tink_route.core.constants import STAGE_TO_SKILLSET, get_default_tink_home
from tink_route.core.exceptions import SkillsetError
from tink_route.metadata import resolve_skillset_members
from tink_route.cli import build_parser, main


def test_resolve_skillset_members_from_pin(tmp_path: Path):
    skillsets_dir = tmp_path / "skillsets"
    skillsets_dir.mkdir()
    pin = skillsets_dir / "testing-skillset.json"
    pin.write_text(json.dumps({"members": ["control-cli", "control-ui", "tdd"]}))

    members = resolve_skillset_members("testing-skillset", tink_home=tmp_path)
    assert members == {"control-cli", "control-ui", "tdd"}

    # Bare name without suffix
    members_bare = resolve_skillset_members("testing", tink_home=tmp_path)
    assert members_bare == {"control-cli", "control-ui", "tdd"}


def test_resolve_skillset_members_missing(tmp_path: Path):
    skillsets_dir = tmp_path / "skillsets"
    skillsets_dir.mkdir()
    with pytest.raises(SkillsetError, match="not found"):
        resolve_skillset_members("nonexistent-skillset", tink_home=tmp_path)


def test_resolve_skillset_members_invalid_name(tmp_path: Path):
    with pytest.raises(SkillsetError, match="Invalid skillset name"):
        resolve_skillset_members("../evil/path", tink_home=tmp_path)


def test_resolve_skillset_members_malformed(tmp_path: Path):
    skillsets_dir = tmp_path / "skillsets"
    skillsets_dir.mkdir()
    pin = skillsets_dir / "broken-skillset.json"
    pin.write_text("invalid json")
    with pytest.raises(SkillsetError, match="Malformed skillset file"):
        resolve_skillset_members("broken-skillset", tink_home=tmp_path)


def test_resolve_stage_mappings():
    assert STAGE_TO_SKILLSET["plan"] == "planning-skillset"
    assert STAGE_TO_SKILLSET["01-plan"] == "planning-skillset"
    assert STAGE_TO_SKILLSET["test"] == "testing-skillset"
    assert STAGE_TO_SKILLSET["04-test"] == "testing-skillset"
    assert STAGE_TO_SKILLSET["build"] == "build-skillset"
    assert STAGE_TO_SKILLSET["03-build"] == "build-skillset"


def test_pstack_stage_skillsets_live():
    """Verify that all 6 stage skillsets in ~/.tink-library/skillsets resolve with pstack skills."""
    tink_home = get_default_tink_home()
    
    plan_members = resolve_skillset_members("planning-skillset", tink_home=tink_home)
    assert "why" in plan_members
    assert "how" in plan_members

    design_members = resolve_skillset_members("design-skillset", tink_home=tink_home)
    assert "architect" in design_members
    assert "blast-radius" in design_members

    build_members = resolve_skillset_members("build-skillset", tink_home=tink_home)
    assert "tdd" in build_members
    assert "deslop" in build_members

    test_members = resolve_skillset_members("testing-skillset", tink_home=tink_home)
    assert "control-cli" in test_members
    assert "control-ui" in test_members
    assert "create-verification-skill" in test_members

    deploy_members = resolve_skillset_members("deployment-skillset", tink_home=tink_home)
    assert "interrogate" in deploy_members

    maintain_members = resolve_skillset_members("maintenance-skillset", tink_home=tink_home)
    assert "principle-fix-root-causes" in maintain_members


def test_cli_parser_skillset_options():
    parser = build_parser()
    args = parser.parse_args(["--skillset", "testing-skillset", "my task"])
    assert args.skillset == "testing-skillset"

    args_stage = parser.parse_args(["--stage", "test", "my task"])
    assert args_stage.stage == "test"
