"""Metadata parsing and library discovery for Agent Skills."""

import json
from pathlib import Path

from .core.constants import RERANK_BODY_EXCERPT_CHARS, get_default_tink_home
from .core.exceptions import SkillsetError, SkillValidationError
from .core.validation import is_valid_skill_name


def parse_skill_metadata(content: str, fallback_name: str) -> dict[str, str]:
    """Extract name and description from SKILL.md frontmatter.

    Supports UTF-8 BOM stripping (META-1), folded and literal YAML blocks
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

            description = joined.strip()

        if closing_dash_idx < 0:
            return {"name": name if is_valid_skill_name(name) else fallback_name, "description": "", "body": content.strip()}

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
            content = skill_file.read_text(encoding="utf-8-sig", errors="replace")
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
        except SkillValidationError:
            continue
        except ValueError:
            raise
        except Exception:
            continue

    return skills


def resolve_skillset(
    skillset_name: str,
    tink_home: Path | None = None,
    project_dir: Path | None = None,
) -> tuple[set[str], set[str]]:
    """Resolve (members, required) from a skillset pin or directory.

    `required` is the optional list of members `tink use` compiles into AGENTS.md.

    Checks, first existing file wins (a present-but-invalid file is an error, never skipped):
    1. <project_dir>/.tink/skillsets/<canonical>.json   (committed project pin)
    2. <project_dir>/.tink/skillsets/<bare>.json
    3. $TINK_HOME/skillsets/<canonical>.json
    4. $TINK_HOME/skillsets/<bare>.json
    5. $TINK_HOME/skillsets/<canonical>/.tink-skillset.json
    6. $TINK_HOME/skillsets/<bare>/.tink-skillset.json
    7. <project_dir>/.agents/skills/<canonical>/.tink-skillset.json
    8. <project_dir>/.agents/skills/<bare>/.tink-skillset.json
    """
    clean_name = skillset_name.strip()
    bare = clean_name[:-9] if clean_name.endswith("-skillset") else clean_name
    canonical = f"{bare}-skillset"

    if not is_valid_skill_name(bare):
        raise SkillsetError(f"Invalid skillset name: '{skillset_name}'")

    home = tink_home or get_default_tink_home()
    skillset_dir = home / "skillsets"

    candidates: list[Path] = []
    if project_dir is not None:
        project_pins = project_dir / ".tink" / "skillsets"
        candidates.extend([
            project_pins / f"{canonical}.json",
            project_pins / f"{bare}.json",
        ])
    candidates += [
        skillset_dir / f"{canonical}.json",
        skillset_dir / f"{bare}.json",
        skillset_dir / canonical / ".tink-skillset.json",
        skillset_dir / bare / ".tink-skillset.json",
    ]
    if project_dir is not None:
        candidates.extend([
            project_dir / ".agents" / "skills" / canonical / ".tink-skillset.json",
            project_dir / ".agents" / "skills" / bare / ".tink-skillset.json",
        ])

    for candidate in candidates:
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except Exception as e:
                raise SkillsetError(f"Malformed skillset file '{candidate}': {e}") from e
            members = data.get("members")
            if not isinstance(members, list):
                raise SkillsetError(f"Skillset file '{candidate}' missing 'members' array")
            required = data.get("required", [])
            if not isinstance(required, list) or not all(isinstance(r, str) for r in required):
                raise SkillsetError(f"Skillset file '{candidate}' has a malformed 'required' array")
            return set(members), set(required)

    raise SkillsetError(
        f"Skillset '{skillset_name}' not found. Searched: "
        + (f"{project_dir / '.tink' / 'skillsets'}, " if project_dir is not None else "")
        + f"{skillset_dir}"
    )
