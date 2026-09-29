"""Validation of skill names."""

import re

SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$", re.ASCII)


def is_valid_skill_name(name: str) -> bool:
    """Validate skill name against ^[a-zA-Z0-9][a-zA-Z0-9_-]{0,63}$.

    Rejects leading hyphens, slashes, whitespace, non-ascii, empty string,
    and names longer than 64 characters.
    """
    if not isinstance(name, str):
        return False
    return bool(SKILL_NAME_PATTERN.fullmatch(name))

