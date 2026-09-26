"""Domain RoutingEngine coordinating client, executor, and ledger."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..adapters.ledger import default_ledger
from .constants import DEFAULT_THRESHOLD, FITS_THRESHOLD, MULTI_DEFAULT_TOP_K, RESERVED_SKILLS
from .exceptions import RoutingError, SkillValidationError
from .models import InstallOutcome, PruneReport, RoutingResult
from .validation import validate_skill_dir_containment

if TYPE_CHECKING:
    from ..adapters.client import JevRouterClient
    from ..adapters.executor import SubprocessExecutor
    from ..adapters.ledger import FilesystemLedger


class RoutingEngine:
    """Pure domain coordinator orchestrating routing, installation, and ephemeral ledger operations."""

    def __init__(
        self,
        client: JevRouterClient | None = None,
        executor: SubprocessExecutor | None = None,
        ledger: FilesystemLedger | None = None,
    ):
        if executor is None:
            from ..adapters.executor import DefaultSubprocessExecutor

            self.executor: SubprocessExecutor = DefaultSubprocessExecutor()
        else:
            self.executor = executor

        self.ledger: FilesystemLedger = ledger if ledger is not None else default_ledger
        self.client: JevRouterClient | None = client

    def install_skill_locked(self, skill_name: str, cwd: Path) -> InstallOutcome:
        """Execute `tink skill add -- <skill_name>` while holding ledger lock."""
        if skill_name in RESERVED_SKILLS:
            return InstallOutcome(
                success=False,
                stdout="",
                stderr=f"Reserved skill name '{skill_name}' cannot be installed",
                code=2,
                skill_path=None,
                references=[],
                scripts=[],
                was_pre_existing=False,
            )
        try:
            skill_dir = validate_skill_dir_containment(cwd, skill_name)
        except SkillValidationError as e:
            return InstallOutcome(
                success=False,
                stdout="",
                stderr=str(e),
                code=2,
                skill_path=None,
                references=[],
                scripts=[],
                was_pre_existing=False,
            )

        was_pre_existing = (skill_dir / "SKILL.md").is_file()

        try:
            code, stdout, stderr = self.executor.run(
                ["tink", "skill", "add", "--", skill_name],
                cwd=cwd,
            )
            success = (code == 0)
        except FileNotFoundError:
            return InstallOutcome(
                success=False,
                stdout="",
                stderr="tink executable not found in PATH",
                code=2,
                skill_path=None,
                references=[],
                scripts=[],
                was_pre_existing=was_pre_existing,
            )

        skill_rel_path = f".agents/skills/{skill_name}/SKILL.md"
        references: list[str] = []
        scripts: list[str] = []

        if success and skill_dir.is_dir():
            ref_dir = skill_dir / "references"
            if ref_dir.is_dir():
                references = [
                    p.relative_to(skill_dir).as_posix()
                    for p in sorted(ref_dir.iterdir())
                    if p.is_file() and not p.name.startswith(".")
                ]
            scr_dir = skill_dir / "scripts"
            if scr_dir.is_dir():
                scripts = [
                    p.relative_to(skill_dir).as_posix()
                    for p in sorted(scr_dir.iterdir())
                    if p.is_file() and not p.name.startswith(".")
                ]

        return InstallOutcome(
            success=success,
            stdout=stdout.strip(),
            stderr=stderr.strip(),
            code=code,
            skill_path=skill_rel_path if success else None,
            references=references,
            scripts=scripts,
            was_pre_existing=was_pre_existing,
        )

    def install_skill(self, skill_name: str, project_dir: Path | None = None) -> InstallOutcome:
        """Safely install skill holding project lock."""
        cwd = project_dir or Path.cwd()
        with self.ledger.lock(cwd):
            return self.install_skill_locked(skill_name, cwd)

    def install_and_track(self, skill_name: str, project_dir: Path, track: bool = True) -> InstallOutcome:
        """Install skill and record in ephemeral ledger under a single project lock."""
        with self.ledger.lock(project_dir):
            outcome = self.install_skill_locked(skill_name, project_dir)
            if not outcome.success or not track or outcome.was_pre_existing:
                return outcome
            try:
                self.ledger.record_ephemeral_skill_locked(project_dir, skill_name)
            except Exception as exc:
                outcome.tracking_error = f"Failed to record ephemeral ledger: {exc}"
            return outcome

    def route(
        self,
        task: str,
        skills: list[dict[str, Any]],
        threshold: float = DEFAULT_THRESHOLD,
        install: bool = False,
        ephemeral: bool = True,
        project_dir: Path | None = None,
        tri_gate: bool = True,
        rerank: bool = True,
        fits_threshold: float = FITS_THRESHOLD,
        multi: bool = False,
        top_k: int = MULTI_DEFAULT_TOP_K,
        skillset: str | None = None,
    ) -> RoutingResult:
        """Route task against an already-loaded skill library, optionally installing the winner."""
        if self.client is None:
            raise RoutingError("JevRouterClient is required for routing")

        res = self.client.route(
            task,
            skills,
            threshold=threshold,
            tri_gate=tri_gate,
            rerank=rerank,
            fits_threshold=fits_threshold,
            multi=multi,
            top_k=top_k,
        )
        if not isinstance(res, RoutingResult):
            raise RoutingError("Invalid routing result from client: expected RoutingResult")
        result = res
        if skillset is not None:
            result.skillset = skillset

        if result.status in ("routed", "multi_routed") and install and result.winner:
            cwd = project_dir or Path.cwd()
            try:
                outcome = self.install_and_track(result.winner, cwd, track=ephemeral)
            except Exception as exc:
                outcome = InstallOutcome(
                    success=False,
                    stdout="",
                    stderr=str(exc),
                    code=2,
                    skill_path=None,
                    references=[],
                    scripts=[],
                    was_pre_existing=False,
                )

            result.installed = outcome.success
            result.skill_path = outcome.skill_path
            result.references = list(outcome.references) if outcome.success else []
            result.scripts = list(outcome.scripts) if outcome.success else []
            result.install_output = outcome.stdout or outcome.stderr
            if outcome.success:
                if outcome.tracking_error:
                    result.tracking_error = outcome.tracking_error
                else:
                    result.activation = {
                        "mode": "direct_read",
                        "entrypoint": result.skill_path,
                        "references": result.references,
                        "scripts": result.scripts,
                        "restart_required": False,
                        "instruction": "Read SKILL.md directly; mid-session use does not require session restart.",
                    }
            else:
                result.install_error = outcome.stderr or "Installation failed"
        elif result.status in ("routed", "multi_routed") and not install and result.winner:
            result.installed = False
            result.activation = {
                "mode": "install_required",
                "entrypoint": None,
                "references": [],
                "scripts": [],
                "restart_required": False,
                "instruction": f"Run 'tink skill add {result.winner}' to install before reading.",
            }

        if result.installed is None:
            result.installed = False

        return result

    def prune(
        self,
        project_dir: Path,
        dry_run: bool = False,
        all_unpinned: bool = False,
    ) -> PruneReport:
        """Prune ephemeral skills from project."""
        return self.ledger.prune(
            project_dir, dry_run=dry_run, all_unpinned=all_unpinned, executor=self.executor
        )
