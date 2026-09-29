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

RERANK_SHORTLIST_SIZE = 3
RERANK_BODY_EXCERPT_CHARS = 700
FITS_THRESHOLD = 0.30


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


GATE_QUESTIONS = {
    "specialised_workflow": (
        "Is the actual task a specialised workflow rather than an ordinary reply? "
        "A specialised workflow follows a task-specific procedure. It produces a structured deliverable; "
        "inspects, transforms, or modifies a document, dataset, codebase, or system; or shapes its answer "
        "by a method, framing, or audience level of understanding that the user explicitly asks for. "
        "It counts even when the result is prose and no external tool is necessary. "
        "An ordinary reply is conversation, a plain factual answer or explanation with no requested method "
        "or audience, arithmetic, a short creative piece, or an isolated short text transformation."
    ),
}

RERANK_INSTRUCTIONS = (
    "Exactly one of these skills is the right one to load for the user's latest "
    "request. Which one? Read what each actually does, not just its name."
)
