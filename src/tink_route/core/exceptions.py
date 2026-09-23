"""Domain exception hierarchy for tink-route."""


class TinkRouteError(Exception):
    """Base exception for all tink-route domain errors."""


class RoutingError(TinkRouteError, RuntimeError):
    """Raised when routing fails or produces an invalid state."""


class ApiProtocolError(RoutingError):
    """Raised on network, HTTP, or JSON protocol failures talking to TypeSafe API."""


class LedgerError(TinkRouteError):
    """Base exception for ledger, manifest, and file-state errors."""


class ManifestSyntaxError(LedgerError, ValueError):
    """Raised when .tink/skills.toml exists but cannot be parsed."""


class SkillValidationError(TinkRouteError, ValueError):
    """Raised when a skill name or path fails security validation."""


class LockError(LedgerError, RuntimeError):
    """Raised when acquiring or releasing a ledger lock fails."""
