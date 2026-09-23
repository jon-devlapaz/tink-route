"""Core domain models for tink-route."""

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass
class RoutingResult:
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
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {"status": self.status, "task": self.task}
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
        if self.installed is not None:
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
        for k, v in self._extra.items():
            res[k] = v
        return res

    def __getitem__(self, key: str) -> Any:
        d = self.to_dict()
        if key in d:
            return d[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if hasattr(self, key) and key != "_extra":
            setattr(self, key, value)
        else:
            self._extra[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key in self.to_dict()

    def keys(self) -> Any:
        return self.to_dict().keys()

    def values(self) -> Any:
        return self.to_dict().values()

    def items(self) -> Any:
        return self.to_dict().items()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return bool(self.to_dict() == other)
        if isinstance(other, RoutingResult):
            return bool(self.to_dict() == other.to_dict())
        return False


@dataclass
class PruneReport:
    pruned: list[str] = field(default_factory=list)
    preserved: list[str] = field(default_factory=list)
    errors: list[dict[str, Any]] = field(default_factory=list)
    dry_run: bool = False
    count: int = 0
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict[str, Any]:
        res: dict[str, Any] = {
            "pruned": list(self.pruned),
            "preserved": list(self.preserved),
        }
        if not self.dry_run:
            res["errors"] = list(self.errors)
        res["dry_run"] = self.dry_run
        res["count"] = self.count
        for k, v in self._extra.items():
            res[k] = v
        return res

    def __getitem__(self, key: str) -> Any:
        d = self.to_dict()
        if key in d:
            return d[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if hasattr(self, key) and key != "_extra":
            setattr(self, key, value)
        else:
            self._extra[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key in self.to_dict()

    def keys(self) -> Any:
        return self.to_dict().keys()

    def values(self) -> Any:
        return self.to_dict().values()

    def items(self) -> Any:
        return self.to_dict().items()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return bool(self.to_dict() == other)
        if isinstance(other, PruneReport):
            return bool(self.to_dict() == other.to_dict())
        return False


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
    _extra: dict[str, Any] = field(default_factory=dict, repr=False)

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
        for k, v in self._extra.items():
            res[k] = v
        return res

    def __getitem__(self, key: str) -> Any:
        d = self.to_dict()
        if key in d:
            return d[key]
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if hasattr(self, key) and key != "_extra":
            setattr(self, key, value)
        else:
            self._extra[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self.to_dict().get(key, default)

    def __contains__(self, key: object) -> bool:
        if not isinstance(key, str):
            return False
        return key in self.to_dict()

    def keys(self) -> Any:
        return self.to_dict().keys()

    def values(self) -> Any:
        return self.to_dict().values()

    def items(self) -> Any:
        return self.to_dict().items()

    def __iter__(self) -> Iterator[str]:
        return iter(self.to_dict())

    def __len__(self) -> int:
        return len(self.to_dict())

    def __eq__(self, other: object) -> bool:
        if isinstance(other, dict):
            return bool(self.to_dict() == other)
        if isinstance(other, InstallOutcome):
            return bool(self.to_dict() == other.to_dict())
        return False


@dataclass
class SkillMetadata:
    name: str
    description: str
    path: str | None = None

    def to_dict(self) -> dict[str, str]:
        res = {"name": self.name, "description": self.description}
        if self.path is not None:
            res["path"] = self.path
        return res

    def __getitem__(self, key: str) -> str:
        d = self.to_dict()
        if key in d:
            return d[key]
        raise KeyError(key)

    def get(self, key: str, default: str | None = None) -> str | None:
        return self.to_dict().get(key, default)

    def __contains__(self, key: object) -> bool:
        return key in self.to_dict()
