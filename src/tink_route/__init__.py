"""
tink-route: Dynamic Agent Skill Router using TypeSafe Jev.
"""

__version__ = "0.5.1"

from .cli import main
from .client import JevRouterClient
from .ephemeral import (
    load_ephemeral_skills,
    prune_ephemeral_skills,
    record_ephemeral_skill,
)
from .metadata import load_library_skills, parse_skill_metadata

__all__ = [
    "__version__",
    "main",
    "JevRouterClient",
    "load_library_skills",
    "parse_skill_metadata",
    "load_ephemeral_skills",
    "prune_ephemeral_skills",
    "record_ephemeral_skill",
]
