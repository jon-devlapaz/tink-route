# Intent: Dynamic Agent Skill Routing via Tink & Jev

Author: jondev (system architect) & pi. Status: approved.

## Problem
- **Progressive Disclosure Overhead:** Pi and the Agent Skills standard inject every discovered skill's `<name>` and `<description>` into the agent's system prompt on every turn. Across 40+ skills in a library, this burns 2,500–5,000+ tokens per turn and causes attention dilution, prompt pollution, and false-positive activations on standard coding tasks.
- **Inventory Mismatch:** Tink intentionally isolates its cold/warm skill library in `~/.tink/skills/` (not an agent discovery root). Only skills explicitly promoted to `<project>/.agents/skills/` should be visible to agents.
- **Manual Routing Friction:** Currently, selecting the right skill from the library requires manual discovery or verbose multi-turn agent prompting.

## Proposed outcome
- A deterministic CLI utility, `tink-route`, installed at `~/.local/bin/tink-route`.
- **Two-stage semantic gating powered by TypeSafe Jev (`jev-1.13.0`):**
  1. *Stage 1 (Need Gate):* A Noul evaluation (*"Is an external specialist skill strictly required for this task?"*). If $p < 0.55$, exits cleanly with status `no_skill_needed` and zero candidate comparisons.
  2. *Stage 2 (Candidate Selection):* If Stage 1 passes, performs a Jev Choice evaluation across the library skills in `~/.tink/skills/` to identify the most load-bearing skill.
- **Separation of Authority:** Defaults to read-only recommendation (prints winner, confidence, rationale); mutates project state (`tink skill add <winner>`) only when explicitly authorized with `--install` (`-i`).
- **Pi Prompt Hygiene:** `~/.pi/agent/skills/` is cleaned so Pi starts with zero injected skills, eliminating progressive disclosure overhead.

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
