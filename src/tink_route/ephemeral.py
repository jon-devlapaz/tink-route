import contextlib
import json
import os
import subprocess
import tomllib
from pathlib import Path
from typing import Any, Dict, List, Set

try:
    import fcntl
except ImportError:
    fcntl = None

RESERVED_SKILLS = {"manage-tink"}


class ManifestSyntaxError(ValueError):
    """Raised when .tink/skills.toml exists but cannot be parsed."""
    pass


def get_tink_dir(project_dir: Path) -> Path:
    tink_dir = project_dir / ".tink"
    tink_dir.mkdir(parents=True, exist_ok=True)
    return tink_dir


@contextlib.contextmanager
def ephemeral_ledger_lock(project_dir: Path):
    """File lock around ephemeral.json to prevent race conditions during concurrent operations."""
    tink_dir = get_tink_dir(project_dir)
    lock_file = tink_dir / "ephemeral.lock"
    fd = os.open(str(lock_file), os.O_CREAT | os.O_RDWR, 0o644)
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            if fcntl:
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def load_ephemeral_skills(project_dir: Path) -> List[str]:
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


def record_ephemeral_skill(project_dir: Path, skill_name: str) -> None:
    tink_dir = get_tink_dir(project_dir)
    ledger_file = tink_dir / "ephemeral.json"
    with ephemeral_ledger_lock(project_dir):
        skills = load_ephemeral_skills(project_dir)
        if skill_name not in skills:
            skills.append(skill_name)
        data = {"version": 1, "skills": skills}
        temp_file = tink_dir / f"ephemeral.json.tmp.{os.getpid()}"
        temp_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        os.replace(temp_file, ledger_file)


def load_pinned_skills(project_dir: Path) -> Set[str]:
    manifest_file = project_dir / ".tink" / "skills.toml"
    if not manifest_file.is_file():
        return set()
    try:
        content = manifest_file.read_text(encoding="utf-8")
        data = tomllib.loads(content)
    except Exception as e:
        raise ManifestSyntaxError(f"Malformed manifest in {manifest_file}: {e}") from e

    pinned = set()
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


def get_installed_skills(project_dir: Path) -> List[str]:
    skills_dir = project_dir / ".agents" / "skills"
    if not skills_dir.is_dir():
        return []
    installed = []
    for child in skills_dir.iterdir():
        if child.name.startswith("."):
            continue
        if child.is_dir() and (child / "SKILL.md").is_file():
            installed.append(child.name)
    return installed


def prune_ephemeral_skills(project_dir: Path, dry_run: bool = False, all_unpinned: bool = False) -> Dict[str, Any]:
    installed = set(get_installed_skills(project_dir))
    ephemeral_tracked = set(load_ephemeral_skills(project_dir))
    pinned = load_pinned_skills(project_dir)

    # Ownership-safe pruning:
    # Default: only prune skills explicitly recorded as ephemeral by tink-route.
    # If all_unpinned: also sweep unpinned skills not in skills.toml.
    if all_unpinned:
        candidates_to_prune = (ephemeral_tracked & installed) | {
            s for s in installed if s not in pinned and s not in RESERVED_SKILLS
        }
    else:
        candidates_to_prune = ephemeral_tracked & installed

    # Always protect reserved skills and explicitly pinned skills
    prunable = sorted(candidates_to_prune - pinned - RESERVED_SKILLS)
    preserved = sorted(installed - set(prunable))

    if dry_run:
        return {
            "pruned": prunable,
            "preserved": preserved,
            "dry_run": True,
            "count": len(prunable),
        }

    pruned_successfully = []
    errors = []

    for skill in prunable:
        try:
            res = subprocess.run(
                ["tink", "skill", "remove", skill],
                cwd=str(project_dir),
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                pruned_successfully.append(skill)
            else:
                errors.append({"skill": skill, "error": res.stderr.strip() or res.stdout.strip()})
        except FileNotFoundError:
            errors.append({"skill": skill, "error": "tink executable not found in PATH"})

    # Update ephemeral ledger under lock
    with ephemeral_ledger_lock(project_dir):
        current_ephemeral = set(load_ephemeral_skills(project_dir))
        remaining_ephemeral = [s for s in current_ephemeral if s not in pruned_successfully]
        ledger_file = project_dir / ".tink" / "ephemeral.json"
        if remaining_ephemeral:
            temp_file = project_dir / ".tink" / f"ephemeral.json.tmp.{os.getpid()}"
            temp_file.write_text(json.dumps({"version": 1, "skills": remaining_ephemeral}, indent=2) + "\n", encoding="utf-8")
            os.replace(temp_file, ledger_file)
        elif ledger_file.is_file():
            ledger_file.unlink(missing_ok=True)

    return {
        "pruned": pruned_successfully,
        "preserved": preserved,
        "errors": errors,
        "dry_run": False,
        "count": len(pruned_successfully),
    }

