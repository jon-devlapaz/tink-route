"""Ephemeral skill ledger management, re-exporting adapter operations for compatibility."""

from pathlib import Path
from typing import Any

from .adapters.executor import DefaultSubprocessExecutor
from .adapters.ledger import RESERVED_SKILLS, default_ledger
from .core.exceptions import ManifestSyntaxError
from .core.models import PruneReport

_DEFAULT_EXECUTOR = DefaultSubprocessExecutor()


def get_tink_dir(project_dir: Path) -> Path:
    return default_ledger.get_tink_dir(project_dir)


def ephemeral_ledger_lock(project_dir: Path) -> Any:
    return default_ledger.lock(project_dir)


def load_ephemeral_skills(project_dir: Path) -> list[str]:
    return default_ledger.load_ephemeral_skills(project_dir)


def record_ephemeral_skill(project_dir: Path, skill_name: str) -> None:
    default_ledger.record_ephemeral_skill(project_dir, skill_name)


def _record_ephemeral_skill_locked(project_dir: Path, skill_name: str) -> None:
    default_ledger.record_ephemeral_skill_locked(project_dir, skill_name)


def load_pinned_skills(project_dir: Path) -> set[str]:
    return default_ledger.load_pinned_skills(project_dir)


def get_installed_skills(project_dir: Path) -> list[str]:
    return default_ledger.get_installed_skills(project_dir)


def prune_ephemeral_skills(
    project_dir: Path, dry_run: bool = False, all_unpinned: bool = False
) -> PruneReport:
    return default_ledger.prune(
        project_dir, dry_run=dry_run, all_unpinned=all_unpinned, executor=_DEFAULT_EXECUTOR
    )


def _prune_ephemeral_skills_locked(
    project_dir: Path, dry_run: bool, all_unpinned: bool
) -> PruneReport:
    return default_ledger._prune_locked(
        project_dir, dry_run=dry_run, all_unpinned=all_unpinned, executor=_DEFAULT_EXECUTOR
    )


__all__ = [
    "RESERVED_SKILLS",
    "ManifestSyntaxError",
    "get_tink_dir",
    "ephemeral_ledger_lock",
    "load_ephemeral_skills",
    "record_ephemeral_skill",
    "_record_ephemeral_skill_locked",
    "load_pinned_skills",
    "get_installed_skills",
    "prune_ephemeral_skills",
    "_prune_ephemeral_skills_locked",
]
