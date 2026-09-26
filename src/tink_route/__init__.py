"""tink-route: Dynamic Agent Skill Router using TypeSafe Jev."""

__version__ = "0.6.0"

from .adapters.client import JevRouterClient
from .adapters.executor import DefaultSubprocessExecutor, SubprocessExecutor
from .adapters.ledger import FilesystemLedger, default_ledger
from .cli import main
from .core.engine import RoutingEngine
from .core.exceptions import (
    ApiProtocolError,
    LedgerError,
    LockError,
    ManifestSyntaxError,
    RoutingError,
    SkillValidationError,
    TinkRouteError,
)
from .core.models import InstallOutcome, PruneReport, RoutingResult
from .core.validation import is_valid_skill_name, validate_skill_dir_containment
from .metadata import load_library_skills, parse_skill_metadata

load_ephemeral_skills = default_ledger.load_ephemeral_skills
record_ephemeral_skill = default_ledger.record_ephemeral_skill
prune_ephemeral_skills = default_ledger.prune

__all__ = [
    "__version__",
    "main",
    "JevRouterClient",
    "load_library_skills",
    "parse_skill_metadata",
    "load_ephemeral_skills",
    "prune_ephemeral_skills",
    "record_ephemeral_skill",
    "TinkRouteError",
    "RoutingError",
    "ApiProtocolError",
    "LedgerError",
    "ManifestSyntaxError",
    "SkillValidationError",
    "LockError",
    "RoutingResult",
    "PruneReport",
    "InstallOutcome",
    "is_valid_skill_name",
    "validate_skill_dir_containment",
    "RoutingEngine",
    "FilesystemLedger",
    "SubprocessExecutor",
    "DefaultSubprocessExecutor",
]
