"""Metadata parsing and library discovery for Agent Skills."""

from pathlib import Path

from .core.constants import RERANK_BODY_EXCERPT_CHARS
from .core.exceptions import SkillValidationError
from .core.validation import is_valid_skill_name


def parse_skill_metadata(content: str, fallback_name: str) -> dict[str, str]:
    """Extract name and description from SKILL.md frontmatter.

    Supports UTF-8 BOM stripping (META-1), YAML chomping indicators >-, >+, |-, |+
    with paragraph preservation (META-2), and name validation (META-3).
    """
    # META-1: Strip UTF-8 BOM if present
    content = content.lstrip("\ufeff")
    name = fallback_name
    description = ""
    body = ""

    lines = content.splitlines()
    if lines and lines[0].strip() == "---":
        desc_lines: list[str] = []
        in_multiline_desc = False
        block_style = ""
        chomp = ""
        closing_dash_idx = -1

        for idx, line in enumerate(lines[1:], start=1):
            trimmed = line.strip()
            if trimmed == "---":
                closing_dash_idx = idx
                break

            if in_multiline_desc:
                if line.startswith("  ") or line.startswith("\t") or trimmed == "":
                    if line.startswith("  "):
                        desc_lines.append(line[2:])
                    elif line.startswith("\t"):
                        desc_lines.append(line[1:])
                    else:
                        desc_lines.append("")
                    continue
                else:
                    in_multiline_desc = False

            if line.startswith("name:"):
                raw_name = line.split(":", 1)[1].strip()
                candidate = raw_name.strip("\"'")
                if candidate:
                    if not is_valid_skill_name(candidate):
                        raise SkillValidationError(f"Invalid skill name '{candidate}' in frontmatter")
                    name = candidate
                in_multiline_desc = False
            elif line.startswith("description:"):
                rest = line.split(":", 1)[1].strip()
                if rest.startswith(">") or rest.startswith("|"):
                    block_style = rest[0]
                    chomp = rest[1:2] if len(rest) > 1 and rest[1] in ("-", "+") else ""
                    in_multiline_desc = True
                    desc_lines = []
                else:
                    description = rest.strip("\"'")
                    in_multiline_desc = False

        if desc_lines:
            if block_style == ">":
                paragraphs: list[str] = []
                current_p: list[str] = []
                for dl in desc_lines:
                    if dl.strip() == "":
                        if current_p:
                            paragraphs.append(" ".join(current_p))
                            current_p = []
                    else:
                        current_p.append(dl.strip())
                if current_p:
                    paragraphs.append(" ".join(current_p))
                joined = "\n\n".join(paragraphs)
            else:
                joined = "\n".join(dl.rstrip() for dl in desc_lines)

            if chomp == "-":
                description = joined.strip()
            elif chomp == "+":
                description = joined
            else:
                description = joined.strip()

        if closing_dash_idx > 0 and closing_dash_idx + 1 < len(lines):
            body = "\n".join(lines[closing_dash_idx + 1:]).strip()
    else:
        body = content.strip()

    # META-3: Validate skill name
    if not is_valid_skill_name(name):
        raise SkillValidationError(f"Invalid skill name '{name}'")

    res = {"name": name, "description": description.strip()}
    if body:
        res["body"] = body
    return res


def load_library_skills(library_dir: Path) -> list[dict[str, str]]:
    """Scan library directory and return all skills with non-empty descriptions."""
    skills: list[dict[str, str]] = []
    names: set[str] = set()
    if not library_dir.exists() or not library_dir.is_dir():
        return skills

    for child in sorted(library_dir.iterdir()):
        if child.name.startswith("."):
            continue
        skill_file = child / "SKILL.md" if child.is_dir() else (child if child.suffix == ".md" else None)
        if not skill_file or not skill_file.is_file():
            continue

        try:
            # META-1: utf-8-sig to automatically strip BOM on read
            content = skill_file.read_text(encoding="utf-8-sig", errors="ignore")
            meta = parse_skill_metadata(content, fallback_name=child.stem if child.is_file() else child.name)
            if meta.get("description"):
                if meta["name"] in names:
                    raise ValueError(f"Duplicate skill name in library: {meta['name']}")
                names.add(meta["name"])
                skills.append({
                    "name": meta["name"],
                    "description": meta["description"][:300],
                    "description_full": meta["description"],
                    "body": meta.get("body", "")[:RERANK_BODY_EXCERPT_CHARS],
                    "path": str(skill_file.resolve()),
                })
        except ValueError:
            raise
        except Exception:
            continue

    return skills
