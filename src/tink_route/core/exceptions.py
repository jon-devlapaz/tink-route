"""Domain exception hierarchy for tink-route."""


class TinkRouteError(Exception):
    """Base exception for all tink-route domain errors."""


class RoutingError(TinkRouteError, RuntimeError):
    """Raised when routing fails or produces an invalid state."""


class ApiProtocolError(RoutingError):
    """Raised on network, HTTP, or JSON protocol failures talking to TypeSafe API."""


class SkillValidationError(TinkRouteError, ValueError):
    """Raised when a skill name or path fails security validation."""


class SkillsetError(TinkRouteError, ValueError):
    """Raised when a skillset pin or definition is missing or invalid."""
