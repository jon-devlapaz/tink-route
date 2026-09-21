# Intent: Dynamic Agent Skill Routing via Tink & Jev

Author: jondev (system architect) & pi. Status: approved (v0.2.0 iteration).

## Problem
- **Progressive Disclosure Overhead:** Pi and the Agent Skills standard inject every discovered skill's `<name>` and `<description>` into the agent's system prompt on every turn. Across 40+ skills in a library, this burns 2,500–5,000+ tokens per turn and causes attention dilution, prompt pollution, and false-positive activations on standard coding tasks.
- **Inventory Mismatch:** Tink intentionally isolates its cold/warm skill library in `~/.tink/skills/` (not an agent discovery root). Only skills explicitly promoted to `<project>/.agents/skills/` should be visible to agents.
- **Manual Pruning Bookkeeping Friction:** During dogfooding, agents successfully route and install skills, but the manual cleanup requirement (`tink skill remove <name>` per `AGENTS.md`) is a recurring structural friction point. When forgotten, ad-hoc transient skills accumulate in `.agents/skills/`, diluting project hygiene.

## Proposed outcome
- A deterministic CLI utility, `tink-route`, installed at `~/.local/bin/tink-route`.
- **Ephemeral Skill Tracking:**
  - When `tink-route -i` installs an ad-hoc skill, it records it in `.tink/ephemeral.json` (or tracks it as unpinned).
  - An optional `--no-ephemeral` flag allows installing permanent skills without ephemeral tagging.
- **Atomic Milestone Pruning (`tink-route prune`):**
  - Sweeps all ephemeral/unpinned skills from `.agents/skills/` using `tink skill remove`.
  - Strictly protects skills pinned in `.tink/skills.toml` and reserved infrastructure skills (`manage-tink`).
  - Supports `--dry-run` to preview skills eligible for pruning.
- **Two-stage semantic gating powered by TypeSafe Jev (`jev-1.13.0`):**
  1. *Stage 1 (Need Gate):* A Noul evaluation ($p < 0.60$ exits with code `1`, `no_skill_needed`).
  2. *Stage 2 (Candidate Selection):* Jev Choice evaluation with top candidate, runner-up, and margin.
- **Semantic Exit Codes:**
  - `0`: Route succeeded / prune executed successfully.
  - `1`: No skill needed / uncertain / no skills to prune.
  - `2`: Fatal/operational error.
- **Single-Hop Agent Reading:** On auto-install (`-i`), output the exact path to `.agents/skills/<name>/SKILL.md`.

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
