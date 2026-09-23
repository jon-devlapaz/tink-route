"""Validation utilities for skill names and path containment."""

import re
from pathlib import Path

from .exceptions import SkillValidationError

SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", re.ASCII)


def is_valid_skill_name(name: str) -> bool:
    """Validate skill name against ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$.

    Rejects leading hyphens, slashes, whitespace, non-ascii, empty string,
    and names longer than 64 characters.
    """
    if not isinstance(name, str):
        return False
    return bool(SKILL_NAME_PATTERN.fullmatch(name))


def validate_skill_dir_containment(project_dir: Path, skill_name: str) -> Path:
    """Validate skill name and ensure its path is strictly contained in .agents/skills."""
    if not is_valid_skill_name(skill_name):
        raise SkillValidationError(
            f"Invalid skill name '{skill_name}': must match ^[a-zA-Z0-9][a-zA-Z0-9_-]{{0,63}}$"
        )

    base_dir = (project_dir / ".agents" / "skills").resolve()
    target_dir = (project_dir / ".agents" / "skills" / skill_name).resolve()

    if target_dir == base_dir or not target_dir.is_relative_to(base_dir):
        raise SkillValidationError(
            f"Skill path traversal detected: '{target_dir}' is not strictly contained in '{base_dir}'"
        )

    return target_dir
