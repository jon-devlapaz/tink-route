"""Core domain models for tink-route."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class RoutingResult:
    status: str
    task: str = ""
    skillset: str | None = None
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
    installed: bool | None = None
    skill_path: str | None = None
    references: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    install_output: str | None = None
    install_error: str | None = None
    tracking_error: str | None = None
    activation: dict[str, Any] | None = None
    fits: dict[str, float] | None = None
    shortlist: list[str] | None = None
    candidates: list[dict[str, Any]] | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {"status": self.status, "task": self.task}
        if self.skillset is not None:
            res["skillset"] = self.skillset
        if self.specialist_noul is not None:
            res["specialist_noul"] = self.specialist_noul
        if self.winner is not None:
            res["winner"] = self.winner
        if self.probability is not None:
            res["probability"] = self.probability
        if self.top_candidate is not None:
            res["top_candidate"] = self.top_candidate
        if self.runner_up is not None:
            res["runner_up"] = self.runner_up
        if self.runner_up_probability is not None:
            res["runner_up_probability"] = self.runner_up_probability
        if self.margin is not None:
            res["margin"] = self.margin
        if self.confidence is not None:
            res["confidence"] = self.confidence
        if self.threshold is not None:
            res["threshold"] = self.threshold
        res["elapsed_ms"] = self.elapsed_ms
        if self.installed is not None:
            res["installed"] = self.installed
        if self.skill_path is not None:
            res["skill_path"] = self.skill_path
        res["references"] = list(self.references)
        res["scripts"] = list(self.scripts)
        if self.install_output is not None:
            res["install_output"] = self.install_output
        if self.install_error is not None:
            res["install_error"] = self.install_error
        if self.tracking_error is not None:
            res["tracking_error"] = self.tracking_error
        if self.activation is not None:
            res["activation"] = dict(self.activation)
        if self.fits is not None:
            res["fits"] = dict(self.fits)
        if self.shortlist is not None:
            res["shortlist"] = list(self.shortlist)
        if self.candidates is not None:
            res["candidates"] = [dict(c) for c in self.candidates]
        return res


@dataclass
class PruneReport:
    pruned: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False
    count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pruned": list(self.pruned),
            "preserved": list(self.preserved),
            "errors": list(self.errors),
            "dry_run": self.dry_run,
            "count": self.count,
        }


@dataclass
class InstallOutcome:
    success: bool
    stdout: str
    stderr: str
    code: int
    skill_path: str | None = None
    references: list[str] = field(default_factory=list)
    scripts: list[str] = field(default_factory=list)
    was_pre_existing: bool = False
    tracking_error: str | None = None
    install_output: str | None = None

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "success": self.success,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "code": self.code,
            "skill_path": self.skill_path,
            "references": list(self.references),
            "scripts": list(self.scripts),
            "was_pre_existing": self.was_pre_existing,
        }
        if self.tracking_error is not None:
            res["tracking_error"] = self.tracking_error
        if self.install_output is not None:
            res["install_output"] = self.install_output
        return res
