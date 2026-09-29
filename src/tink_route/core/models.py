"""Core domain models for tink-route."""

from dataclasses import dataclass
from typing import Any


@dataclass
class RoutingResult:
    """One routing decision. `status` is "routed" or the reason nothing was chosen."""

    status: str
    task: str = ""
    specialist_noul: float | None = None
    threshold: float | None = None
    elapsed_ms: int = 0
    winner: str | None = None
    probability: float | None = None
    confidence: float | None = None
    top_candidate: str | None = None
    runner_up: str | None = None
    runner_up_probability: float | None = None
    margin: float | None = None
    fits: dict[str, float] | None = None
    shortlist: list[str] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "task": self.task,
            "specialist_noul": self.specialist_noul,
            "winner": self.winner,
            "probability": self.probability,
            "confidence": self.confidence,
            "threshold": self.threshold,
            "runner_up": self.runner_up,
            "runner_up_probability": self.runner_up_probability,
            "margin": self.margin,
            "elapsed_ms": self.elapsed_ms,
            "fits": dict(self.fits) if self.fits is not None else None,
            "shortlist": list(self.shortlist) if self.shortlist is not None else None,
        }
