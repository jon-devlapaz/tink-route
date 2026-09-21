from pathlib import Path
from typing import Dict, List


def parse_skill_metadata(content: str, fallback_name: str) -> Dict[str, str]:
    """Extract name and description from SKILL.md frontmatter."""
    name = fallback_name
    description = ""

    lines = content.splitlines()
    if lines and lines[0].strip() == "---":
        desc_lines = []
        in_multiline_desc = False

        for line in lines[1:]:
            trimmed = line.strip()
            if trimmed == "---":
                break
            if line.startswith("name:"):
                name = line.split(":", 1)[1].strip() or fallback_name
                in_multiline_desc = False
            elif line.startswith("description:"):
                rest = line.split(":", 1)[1].strip()
                if rest in (">", "|"):
                    in_multiline_desc = True
                else:
                    description = rest
                    in_multiline_desc = False
            elif in_multiline_desc:
                if line.startswith("  ") or line.startswith("\t"):
                    desc_lines.append(trimmed)
                else:
                    in_multiline_desc = False

        if desc_lines:
            description = " ".join(desc_lines)

    return {"name": name, "description": description.strip()}


def load_library_skills(library_dir: Path) -> List[Dict[str, str]]:
    """Scan library directory and return all skills with non-empty descriptions."""
    skills = []
    if not library_dir.exists() or not library_dir.is_dir():
        return skills

    for child in sorted(library_dir.iterdir()):
        if child.name.startswith("."):
            continue
        skill_file = child / "SKILL.md" if child.is_dir() else (child if child.suffix == ".md" else None)
        if not skill_file or not skill_file.is_file():
            continue

        try:
            content = skill_file.read_text(encoding="utf-8", errors="ignore")
            meta = parse_skill_metadata(content, fallback_name=child.stem if child.is_file() else child.name)
            if meta.get("description"):
                skills.append({
                    "name": meta["name"],
                    "description": meta["description"][:300],  # Bound description length for prompt safety
                    "path": str(skill_file.resolve()),
                })
        except Exception:
            continue

    return skills
