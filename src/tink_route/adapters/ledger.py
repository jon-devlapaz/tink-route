"""Filesystem ledger adapter managing ephemeral state, manifest pinning, and locking."""

import contextlib
import json
import os
import sys
import tempfile
import threading
import tomllib
from pathlib import Path
from typing import Any, Iterator

from ..core.exceptions import LockError, ManifestSyntaxError
from ..core.models import PruneReport
from .executor import DefaultSubprocessExecutor, SubprocessExecutor

RESERVED_SKILLS = {"manage-tink"}

_REGISTRY_LOCK = threading.Lock()
_PROJECT_LOCKS: dict[Path, tuple[threading.RLock, int, int | None]] = {}


def _get_lock_backends() -> tuple[Any, Any]:
    """Retrieve fcntl / msvcrt modules, supporting dynamic monkey-patching in tests."""
    f_backend: Any = None
    m_backend: Any = None
    ephem = sys.modules.get("tink_route.ephemeral")

    if ephem is not None and hasattr(ephem, "fcntl"):
        f_backend = ephem.fcntl
    else:
        try:
            import fcntl
            f_backend = fcntl
        except ImportError:
            f_backend = None

    if ephem is not None and hasattr(ephem, "msvcrt"):
        m_backend = ephem.msvcrt
    else:
        try:
            import msvcrt
            m_backend = msvcrt
        except ImportError:
            m_backend = None

    return f_backend, m_backend


class FilesystemLedger:
    """Encapsulates .tink/ephemeral.json, ephemeral.lock, and skills.toml interactions."""

    def get_tink_dir(self, project_dir: Path) -> Path:
        tink_dir = project_dir / ".tink"
        tink_dir.mkdir(parents=True, exist_ok=True)
        return tink_dir

    @contextlib.contextmanager
    def lock(self, project_dir: Path) -> Iterator[None]:
        """Per-project reentrant file lock using strict LIFO order (CONC-1, CONC-2)."""
        proj = project_dir.resolve()
        with _REGISTRY_LOCK:
            if proj in _PROJECT_LOCKS:
                thread_lock, depth, fd = _PROJECT_LOCKS[proj]
            else:
                thread_lock = threading.RLock()
                depth = 0
                fd = None
                _PROJECT_LOCKS[proj] = (thread_lock, depth, fd)

        # 1. Acquire thread lock before OS lock (CONC-2)
        thread_lock.acquire()
        try:
            with _REGISTRY_LOCK:
                if proj in _PROJECT_LOCKS:
                    thread_lock, depth, fd = _PROJECT_LOCKS[proj]
                else:
                    depth = 0
                    fd = None

                if depth == 0:
                    tink_dir = self.get_tink_dir(proj)
                    lock_file = tink_dir / "ephemeral.lock"
                    new_fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
                    f_backend, m_backend = _get_lock_backends()
                    try:
                        if f_backend is not None:
                            f_backend.flock(new_fd, f_backend.LOCK_EX)
                        elif m_backend is not None:
                            m_backend.locking(new_fd, m_backend.LK_LOCK, 1)
                        else:
                            raise LockError("No supported file-locking mechanism on this platform")
                    except BaseException:
                        os.close(new_fd)
                        raise
                    _PROJECT_LOCKS[proj] = (thread_lock, 1, new_fd)
                else:
                    _PROJECT_LOCKS[proj] = (thread_lock, depth + 1, fd)

            yield

        finally:
            try:
                with _REGISTRY_LOCK:
                    if proj in _PROJECT_LOCKS:
                        lock_obj, depth, current_fd = _PROJECT_LOCKS[proj]
                        if depth <= 1:
                            f_backend, m_backend = _get_lock_backends()
                            try:
                                if current_fd is not None:
                                    if f_backend is not None:
                                        f_backend.flock(current_fd, f_backend.LOCK_UN)
                                    elif m_backend is not None:
                                        os.lseek(current_fd, 0, os.SEEK_SET)
                                        m_backend.locking(current_fd, m_backend.LK_UNLCK, 1)
                            finally:
                                if current_fd is not None:
                                    try:
                                        os.close(current_fd)
                                    except OSError:
                                        pass
                                _PROJECT_LOCKS.pop(proj, None)
                        else:
                            _PROJECT_LOCKS[proj] = (lock_obj, depth - 1, current_fd)
            finally:
                # Release thread lock after OS lock is released and fd closed (CONC-2)
                thread_lock.release()

    def load_ephemeral_skills(self, project_dir: Path) -> list[str]:
        ledger_file = project_dir / ".tink" / "ephemeral.json"
        if not ledger_file.is_file():
            return []
        try:
            data = json.loads(ledger_file.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                return []
            raw_skills = data.get("skills")
            if not isinstance(raw_skills, list):
                return []
            return [s for s in raw_skills if isinstance(s, str)]
        except Exception:
            return []

    def _write_ephemeral_json(self, project_dir: Path, skills: list[str]) -> None:
        """Atomic write using tempfile, fsync, and replace (CONC-4, CONC-5)."""
        tink_dir = self.get_tink_dir(project_dir)
        ledger_file = tink_dir / "ephemeral.json"
        data = {"version": 1, "skills": skills}
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "w",
                dir=tink_dir,
                prefix="ephemeral.json.tmp.",
                delete=False,
                encoding="utf-8",
            ) as tf:
                temp_path = Path(tf.name)
                tf.write(json.dumps(data, indent=2) + "\n")
                tf.flush()
                os.fsync(tf.fileno())
            os.replace(temp_path, ledger_file)
            temp_path = None
        finally:
            if temp_path is not None and temp_path.exists():
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def record_ephemeral_skill_locked(self, project_dir: Path, skill_name: str) -> None:
        skills = self.load_ephemeral_skills(project_dir)
        if skill_name not in skills:
            skills.append(skill_name)
        self._write_ephemeral_json(project_dir, skills)

    def record_ephemeral_skill(self, project_dir: Path, skill_name: str) -> None:
        with self.lock(project_dir):
            self.record_ephemeral_skill_locked(project_dir, skill_name)

    def load_pinned_skills(self, project_dir: Path) -> set[str]:
        manifest_file = project_dir / ".tink" / "skills.toml"
        if not manifest_file.is_file():
            return set()
        try:
            content = manifest_file.read_text(encoding="utf-8")
            data = tomllib.loads(content)
        except Exception as e:
            raise ManifestSyntaxError(f"Malformed manifest in {manifest_file}: {e}") from e

        pinned: set[str] = set()
        skills_section = data.get("skills")
        if isinstance(skills_section, list):
            for item in skills_section:
                if isinstance(item, dict) and "name" in item and isinstance(item["name"], str):
                    pinned.add(item["name"])
        elif isinstance(skills_section, dict):
            for k, v in skills_section.items():
                if isinstance(v, dict) and "name" in v and isinstance(v["name"], str):
                    pinned.add(v["name"])
                elif isinstance(k, str):
                    pinned.add(k)
        return pinned

    def get_installed_skills(self, project_dir: Path) -> list[str]:
        skills_dir = project_dir / ".agents" / "skills"
        if not skills_dir.is_dir():
            return []
        installed: list[str] = []
        for child in sorted(skills_dir.iterdir()):
            if child.name.startswith("."):
                continue
            if child.is_dir() and (child / "SKILL.md").is_file():
                installed.append(child.name)
        return installed

    def prune(
        self,
        project_dir: Path,
        dry_run: bool = False,
        all_unpinned: bool = False,
        executor: SubprocessExecutor | None = None,
    ) -> PruneReport:
        if dry_run:
            return self._prune_locked(
                project_dir, dry_run=True, all_unpinned=all_unpinned, executor=executor
            )
        with self.lock(project_dir):
            return self._prune_locked(
                project_dir, dry_run=False, all_unpinned=all_unpinned, executor=executor
            )

    def _prune_locked(
        self,
        project_dir: Path,
        dry_run: bool,
        all_unpinned: bool,
        executor: SubprocessExecutor | None = None,
    ) -> PruneReport:
        exec_handler = executor or DefaultSubprocessExecutor()
        installed = set(self.get_installed_skills(project_dir))
        ephemeral_tracked = set(self.load_ephemeral_skills(project_dir))
        pinned = self.load_pinned_skills(project_dir)

        if all_unpinned:
            candidates_to_prune = (ephemeral_tracked & installed) | {
                s for s in installed if s not in pinned and s not in RESERVED_SKILLS
            }
        else:
            candidates_to_prune = ephemeral_tracked & installed

        prunable = sorted(candidates_to_prune - pinned - RESERVED_SKILLS)
        preserved = sorted(installed - set(prunable))

        if dry_run:
            return PruneReport(
                pruned=prunable,
                preserved=preserved,
                dry_run=True,
                count=len(prunable),
            )

        pruned_successfully: list[str] = []
        errors: list[dict[str, Any]] = []

        for skill in prunable:
            try:
                code, stdout, stderr = exec_handler.run(
                    ["tink", "skill", "remove", skill],
                    cwd=project_dir,
                )
                if code == 0:
                    pruned_successfully.append(skill)
                else:
                    errors.append({"skill": skill, "error": stderr.strip() or stdout.strip()})
            except FileNotFoundError:
                errors.append({"skill": skill, "error": "tink executable not found in PATH"})

        # CONC-6: Ghost skill cleanup
        current_ephemeral = set(self.load_ephemeral_skills(project_dir))
        installed_now = set(self.get_installed_skills(project_dir))
        remaining_ephemeral = [
            s for s in current_ephemeral
            if s not in pruned_successfully and s in installed_now
        ]

        ledger_file = project_dir / ".tink" / "ephemeral.json"
        if remaining_ephemeral:
            self._write_ephemeral_json(project_dir, remaining_ephemeral)
        elif ledger_file.is_file():
            ledger_file.unlink(missing_ok=True)

        return PruneReport(
            pruned=pruned_successfully,
            preserved=preserved,
            errors=errors,
            dry_run=False,
            count=len(pruned_successfully),
        )
