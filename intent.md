# Intent: Dynamic Agent Skill Routing via Tink & Jev

Author: jondev (system architect) & pi. Status: approved (v0.4.0 iteration).

## Problem
- **Progressive Disclosure Overhead:** Pi and the Agent Skills standard inject every discovered skill's `<name>` and `<description>` into the agent's system prompt on every turn. Across 40+ skills in a library, this burns 2,500–5,000+ tokens per turn and causes attention dilution, prompt pollution, and false-positive activations on standard coding tasks.
- **Inventory Mismatch:** Tink intentionally isolates its cold/warm skill library in `~/.tink/skills/` (not an agent discovery root). Only skills explicitly promoted to `<project>/.agents/skills/` should be visible to agents.
- **Issue #1 (Prune Ownership Safety):** `tink-route prune` removed unpinned skills that Tink Route did not install (e.g. manual `tink skill add` or other agent harness installs like Cursor). Pruning must be ownership-safe and ledger-only by default.
- **Issue #2 (Mid-Session Activation Contract):** After `--install`, it was ambiguous whether an active agent session needed a restart or re-scan. The contract must explicitly define single-hop direct reading (`read(skill_path)`) without session restarts.
- **Issue #3 (Batched Confidence Semantics):** In multi-batch Stage 2 routing (`len(skills) > BATCH_SIZE`), single-winner and zero-winner branches hardcoded synthetic `0.85` / `0.90` confidence values instead of preserving authentic Jev API responses.

## Proposed outcome
- **Ownership-Safe Pruning (Resolves #1):**
  - Default `tink-route prune` is strictly ledger-only: removes only skills recorded in `<project>/.tink/ephemeral.json`.
  - Foreign, manual, or other-harness installed skills are strictly preserved.
  - An optional `--all-unpinned` flag enables sweeping all unpinned skills if explicitly requested.
- **Explicit Activation Contract & Payload (Resolves #2):**
  - Define the canonical post-install activation contract: mid-session skills are usable immediately via direct file reading without session restart.
  - `--json` payload includes an `activation` object with actionable guidance and paths.
- **Authentic Jev Batched Semantics (Resolves #3):**
  - Eliminate all hardcoded `0.85` / `0.90` synthetic confidence values.
  - Retain authentic Jev `confidence` and `probabilities` across all single-batch, multi-batch, and zero-winner reduction paths.
  - Comprehensive unit test coverage for `len(skills) > BATCH_SIZE`.
- **Semantic Exit Codes:** `0` (routed/pruned), `1` (unrouted/no-op), `2` (error).
- **Single-Hop Reading & Asset Surfacing:** Output `SKILL.md` path plus bundled references and scripts.

## Affected users and systems
- **Users:** Developers interacting with AI agents (Pi, Claude Code, Codex).
- **Systems:**
  - `tink` CLI (`/Users/jondev/.local/bin/tink`) & `~/.tink/skills/` library.
  - Project `.agents/skills/` directory.
  - TypeSafe Jev API (`api.typesafe.ai` via `TYPESAFE_API_KEY`).
  - Pi Coding Agent session prompt builder.

## Constraints
- **Universal Portability:** The routing utility must be a standalone CLI that works across any agent harness (Pi, Claude Code, Codex, shell), not locked into Pi-specific TypeScript plugins.
- **Tink Invariant:** Respect Tink's separation between inspection and mutation authority. Do not mutate `.agents/skills/` without the `--install` flag.
- **Latency & Cost Budget:** Must finish within ~1.5s total latency. The two-stage gate must prevent candidate evaluation on everyday coding turns.
- **Deterministic Dependencies:** Use standard Python 3.11+ without complex runtime requirements.

## Open questions
- *Answered via Jev:* Should trigger be reactive or proactive? **Reactive** ($p = 1.00$).
- *Answered via Jev:* Should pruning be ephemeral or milestone-based? **Milestone / session-end** ($p = 0.99$).
- *Answered via Jev:* Should candidate selection be single-stage or two-stage? **Two-stage** ($p = 1.00$).
