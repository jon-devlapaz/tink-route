"""tink-route: Dynamic Agent Skill Router using TypeSafe Jev."""

__version__ = "0.11.0"

from .adapters.client import JevRouterClient
from .adapters.executor import DefaultSubprocessExecutor, SubprocessExecutor
from .cli import main
from .core.exceptions import (
    ApiProtocolError,
    RoutingError,
    SkillsetError,
    SkillValidationError,
    TinkRouteError,
)
from .core.models import RoutingResult
from .core.validation import is_valid_skill_name
from .metadata import load_library_skills, parse_skill_metadata, resolve_skillset

__all__ = [
    "__version__",
    "main",
    "JevRouterClient",
    "load_library_skills",
    "parse_skill_metadata",
    "resolve_skillset",
    "TinkRouteError",
    "RoutingError",
    "ApiProtocolError",
    "SkillsetError",
    "SkillValidationError",
    "RoutingResult",
    "is_valid_skill_name",
    "SubprocessExecutor",
    "DefaultSubprocessExecutor",
]
