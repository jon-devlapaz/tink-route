"""Core constants for tink-route."""

DEFAULT_MODEL = "jev-1.13.0"
DEFAULT_THRESHOLD = 0.60
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
    "acts_on_user_system": (
        "Is the assistant being asked to act on the user's files, accounts, devices, "
        "or online services, rather than only to explain or advise?"
    ),
    "would_follow_documented_procedure": (
        "Would a careful expert answering this consult a specific documented procedure "
        "or set of commands, rather than answering from general understanding?"
    ),
    "prose_suffices": (
        "Could a knowledgeable generalist fully satisfy this request in prose, with "
        "no tools, no documentation, and no access to the user's files or accounts?"
    ),
}

RERANK_INSTRUCTIONS = (
    "Exactly one of these skills is the right one to load for the user's latest "
    "request. Which one? Read what each actually does, not just its name."
)
