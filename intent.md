# Intent: Dynamic Agent Skill Routing via Tink & Jev

Author: jondev (system architect) & pi. Status: approved (v0.5.1 iteration).

## Problem
- **Progressive Disclosure Overhead:** Pi and the Agent Skills standard inject every discovered skill's `<name>` and `<description>` into the agent's base system prompt on every turn. Across 40+ skills in a library, this burns 2,500–5,000+ tokens per turn and causes attention dilution, prompt pollution, and false-positive activations on standard coding tasks.
- **Inventory Mismatch:** Tink intentionally isolates its cold/warm skill library in `~/.tink-library/skills/` (or `$TINK_HOME/skills`) (not an agent discovery root). Only skills explicitly promoted to `<project>/.agents/skills/` should be visible to agents.
- **Audited Boundary Defects (v0.5.1 Audit Remediation):**
  1. **Concurrent Ledger Writes:** Concurrent `tink-route -i` calls race on `ephemeral.json`, overwriting each other's records. Must use process/thread file locking and atomic temporary file replacement.
  2. **Partial Prune Failures Reporting Success (0):** When `tink-route prune` prunes some skills but fails on others, it returns `0`. Partial failures must return operational error code `2`.
  3. **Unhandled Ledger Write Exceptions:** Uncaught `OSError` / `PermissionError` when writing `ephemeral.json` crashes the CLI with a traceback. Must catch ledger write exceptions, fail safely, and exit code `2`.
  4. **Unknown / Path Traversal API Candidate Validation:** An unlisted candidate (e.g. `../outside` or hallucinated skill) returned by the API is accepted as `status: "routed"`. Must strictly validate candidate membership against known library names and reject path traversal or unlisted choices with operational error `2`.

## Proposed outcome (v0.5.1)
- **Concurrent Ledger Locking (`ephemeral.py`):** Use `fcntl.flock` on `.tink/ephemeral.lock` with atomic file replace (`os.replace`) to prevent lost records during concurrent installs and prunes.
- **Strict Error Exit Code on Partial Prune (`cli.py`):** If `res.get("errors")` is non-empty, `tink-route prune` returns exit code `2` (operational error) instead of `0`.
- **Protected Ledger I/O (`cli.py`):** Wrap `record_ephemeral_skill` in try/except; if ledger write fails, return clean operational error `2` without traceback.
- **Strict API Candidate & Identifier Validation (`client.py`):** Validate that `winner` is strictly an element of candidate skill criteria and matches safe skill name syntax (no `..`, `/`, `\`). Reject unlisted candidates with a `RuntimeError` (exit code `2`).

## Affected users and systems
- **Users:** Developers and automated agent harnesses (Pi, Claude Code, Cursor, Codex).
- **Systems:**
  - `tink` CLI (`tink skill add`, `tink skill remove`) & `~/.tink-library/skills/` library ($TINK_HOME-compatible).
  - Project `.agents/skills/` directory and `.tink/ephemeral.json` ledger.
  - TypeSafe Jev API (`api.typesafe.ai` via `TYPESAFE_API_KEY`).
  - Automated agent pipelines executing dynamic routing.

## Constraints
- **Universal Portability:** Standalone CLI, standard Python 3.11+ zero third-party dependencies (`urllib.request`, `tomllib`, `json`).
- **Data Safety:** Never delete manual, foreign, or manifest-pinned skills.
- **Semantic Exit Code Invariants:** `0` (success / routed / cleanly pruned), `1` (unrouted: `no_skill_needed`, `no_match`, `uncertain`), `2` (operational error: missing key/library, failed install, syntax error in manifest).


## Open questions
- *Answered via Jev:* Should trigger be reactive or proactive? **Reactive** ($p = 1.00$).
- *Answered via Jev:* Should pruning be ephemeral or milestone-based? **Milestone / session-end** ($p = 0.99$).
- *Answered via Jev:* Should candidate selection be single-stage or two-stage? **Two-stage** ($p = 1.00$).
