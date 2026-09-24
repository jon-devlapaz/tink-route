"""Core constants for tink-route."""

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
