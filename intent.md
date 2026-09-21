# Intent: Dynamic Agent Skill Routing via Tink & Jev

Author: jondev (system architect) & pi. Status: approved (v0.5.0 audit remediation).

## Problem
- **Progressive Disclosure Overhead:** Pi and the Agent Skills standard inject every discovered skill's `<name>` and `<description>` into the agent's system prompt on every turn. Across 40+ skills in a library, this burns 2,500–5,000+ tokens per turn and causes attention dilution, prompt pollution, and false-positive activations on standard coding tasks.
- **Inventory Mismatch:** Tink intentionally isolates its cold/warm skill library in `~/.tink/skills/` (not an agent discovery root). Only skills explicitly promoted to `<project>/.agents/skills/` should be visible to agents.
- **Audited Boundary Defects (v0.4.0 Critique):**
  1. **P1 — Existing Manual Installs Adopted & Deleted:** When `tink-route -i` routes to a skill that was *already* installed manually in `.agents/skills/`, it unconditionally records it in `.tink/ephemeral.json`. Subsequent `tink-route prune` deletes the user's manual installation.
  2. **P1 — Valid Manifest Syntax Bypasses Pin Protection:** `load_pinned_skills` uses an unescaped regex `name = "..."` that misses valid TOML forms (e.g. `"name" = "cro"`, single quotes, or table headers like `[skills.cro]`). Unpinned pruning deletes pinned skills. Furthermore, unreadable manifests fail open (returning an empty set) rather than failing closed.
  3. **P1 — Failed Install Reports Exit 0 & Emits Phantom Activation:** When `tink skill add` fails, `cli.py` still reports `status: "routed"`, exits `0`, and attaches a `direct_read` activation block pointing to a nonexistent path. In recommendation-only mode (without `-i`), `activation` is also emitted with a nonexistent entrypoint path.
  4. **P2 — Unhandled Exceptions & Missing Binary Errors:** A malformed ledger `{"skills": null}` or unhashable objects inside `skills` raises unhandled `TypeError` tracebacks. If the `tink` executable is missing, uncaught `FileNotFoundError` crashes the CLI.
  5. **P2 — Batch Reduction Misstates Unavailable Expertise:** When all batches in `len(skills) > BATCH_SIZE` return `__no_match__`, the reducer forces `winner = __no_skill__` and fabricates `0.0`, converting a specialist task with no available skill (`no_match`) into `no_skill_needed` (falsely claiming standard tools suffice).
  6. **P2 — Frontmatter YAML Quote Retention:** Frontmatter parsing retains surrounding quotes (`name: "cro"` becomes `'"cro"'`), corrupting skill names and causing downstream command failures.
  7. **P2 — Cross-Platform Path Interoperability:** Reference and script paths use `str(p.relative_to(...))` producing Windows backslashes in JSON payloads.
  8. **P2 — Clean `--dry-run` Exits 1 Instead of 0:** `tink-route prune --dry-run` exits 1 when 0 skills are eligible, violating the convention that a clean inspection check exits 0.

## Proposed outcome (v0.5.0)
- **Pre-Existing Install Protection:** Check if `.agents/skills/<winner>` exists *before* install. If it was already present and not previously tracked in `.tink/ephemeral.json`, preserve its manual ownership and do not record it in the ephemeral ledger.
- **Fail-Closed Standard TOML Parsing:** Use Python 3.11 stdlib `tomllib.loads` to parse `.tink/skills.toml`. If the manifest exists but cannot be parsed, fail closed (raise an operational error / preserve all skills) rather than deleting pinned skills.
- **Strict Exit Code & Activation Contract:**
  - If `-i` is requested and `tink skill add` fails: exit `2` (operational error), do not record ephemeral, and do not emit a `direct_read` activation block.
  - If `-i` is NOT requested (recommendation-only): omit the `direct_read` activation block (or set `mode: "install_required"` with `entrypoint: null`), preventing agents from attempting to read uninstalled paths.
- **Robust Ledger Validation & Process Execution:**
  - Validate that `ephemeral.json` `skills` is a list of strings (`[s for s in skills if isinstance(s, str)]`). Recover cleanly without crashing.
  - Wrap `subprocess.run` calls to handle `FileNotFoundError` (missing `tink`) with a clean exit code `2`.
- **Authentic `no_match` Batch Reduction:** If all batches return `__no_match__`, report `status: "no_match"` and preserve authentic Stage 1 `specialist_noul` and Stage 2 responses without fabricating `no_skill_needed`.
- **Clean Frontmatter & POSIX Paths:** Strip surrounding quotes in frontmatter values. Normalize all output paths with `.as_posix()`.
- **Prune Dry-Run Convention:** `prune --dry-run` exits `0` when completed successfully, regardless of candidate count.

## Affected users and systems
- **Users:** Developers and automated agent harnesses (Pi, Claude Code, Cursor, Codex).
- **Systems:**
  - `tink` CLI (`tink skill add`, `tink skill remove`) & `~/.tink/skills/` library.
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
