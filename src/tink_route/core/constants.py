"""Core constants for tink-route."""

import os
from pathlib import Path

DEFAULT_MODEL = "jev-1.13.0"
DEFAULT_THRESHOLD = 0.60
GATE_ABSTAIN = 0.30
TYPESAFE_API_URL = "https://api.typesafe.ai/v1/systemone"
BATCH_SIZE = 24

NO_SKILL_SENTINEL = "__no_skill__"
NO_MATCH_SENTINEL = "__no_match__"

RESERVED_SKILLS = frozenset({"manage-tink"})

RERANK_SHORTLIST_SIZE = 3
RERANK_BODY_EXCERPT_CHARS = 700
FITS_THRESHOLD = 0.30

MULTI_DEFAULT_TOP_K = 3
MULTI_MAX_TOP_K = 10


def get_default_tink_home() -> Path:
    tink_home = os.environ.get("TINK_HOME")
    if tink_home:
        home = Path(tink_home).expanduser()
        if not home.is_absolute():
            home = Path.cwd() / home
        return home
    return Path.home() / ".tink-library"


def get_default_library_path() -> Path:
    tink_home = os.environ.get("TINK_HOME")
    if tink_home:
        home = Path(tink_home).expanduser()
        if not home.is_absolute():
            home = Path.cwd() / home
        return home / "skills"
    return Path.home() / ".tink-library" / "skills"


STAGE_TO_SKILLSET: dict[str, str] = {
    "plan": "planning-skillset",
    "01-plan": "planning-skillset",
    "planning": "planning-skillset",
    "design": "design-skillset",
    "02-design": "design-skillset",
    "build": "build-skillset",
    "03-build": "build-skillset",
    "test": "testing-skillset",
    "04-test": "testing-skillset",
    "testing": "testing-skillset",
    "deploy": "deployment-skillset",
    "05-deploy": "deployment-skillset",
    "deployment": "deployment-skillset",
    "maintain": "maintenance-skillset",
    "06-maintain": "maintenance-skillset",
    "maintenance": "maintenance-skillset",
}

GATE_QUESTIONS = {
    "specialised_workflow": (
        "Is the actual task a specialised workflow rather than an ordinary reply? "
        "A specialised workflow produces a structured deliverable or inspects, transforms, "
        "or modifies a document, dataset, codebase, or system using task-specific procedures. "
        "Document-level work counts even when its result is prose and no external tool is necessary. "
        "An ordinary reply is conversation, a general explanation, arithmetic, or an isolated short text transformation."
    ),
}

RERANK_INSTRUCTIONS = (
    "Exactly one of these skills is the right one to load for the user's latest "
    "request. Which one? Read what each actually does, not just its name."
)
