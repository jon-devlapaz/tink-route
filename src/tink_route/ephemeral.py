import json
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Set

RESERVED_SKILLS = {"manage-tink"}


def get_tink_dir(project_dir: Path) -> Path:
    tink_dir = project_dir / ".tink"
    tink_dir.mkdir(parents=True, exist_ok=True)
    return tink_dir


def load_ephemeral_skills(project_dir: Path) -> List[str]:
    ledger_file = project_dir / ".tink" / "ephemeral.json"
    if not ledger_file.is_file():
        return []
    try:
        data = json.loads(ledger_file.read_text(encoding="utf-8"))
        return data.get("skills", [])
    except Exception:
        return []


def record_ephemeral_skill(project_dir: Path, skill_name: str) -> None:
    tink_dir = get_tink_dir(project_dir)
    ledger_file = tink_dir / "ephemeral.json"
    skills = load_ephemeral_skills(project_dir)
    if skill_name not in skills:
        skills.append(skill_name)
    data = {"version": 1, "skills": skills}
    ledger_file.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def load_pinned_skills(project_dir: Path) -> Set[str]:
    manifest_file = project_dir / ".tink" / "skills.toml"
    if not manifest_file.is_file():
        return set()
    pinned = set()
    try:
        content = manifest_file.read_text(encoding="utf-8")
        # Match `name = "skill-name"` in TOML without external toml parser
        matches = re.findall(r'name\s*=\s*["\']([^"\']+)["\']', content)
        pinned.update(matches)
    except Exception:
        pass
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

    # Update ephemeral ledger
    remaining_ephemeral = [s for s in ephemeral_tracked if s not in pruned_successfully]
    ledger_file = project_dir / ".tink" / "ephemeral.json"
    if remaining_ephemeral:
        ledger_file.write_text(json.dumps({"version": 1, "skills": remaining_ephemeral}, indent=2) + "\n", encoding="utf-8")
    elif ledger_file.is_file():
        ledger_file.unlink(missing_ok=True)

    return {
        "pruned": pruned_successfully,
        "preserved": preserved,
        "errors": errors,
        "dry_run": False,
        "count": len(pruned_successfully),
    }
