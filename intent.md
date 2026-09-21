# Intent: Dynamic Agent Skill Routing via Tink & Jev

Author: jondev (system architect) & pi. Status: approved (v0.3.0 iteration).

## Problem
- **Progressive Disclosure Overhead:** Pi and the Agent Skills standard inject every discovered skill's `<name>` and `<description>` into the agent's system prompt on every turn. Across 40+ skills in a library, this burns 2,500–5,000+ tokens per turn and causes attention dilution, prompt pollution, and false-positive activations on standard coding tasks.
- **Inventory Mismatch:** Tink intentionally isolates its cold/warm skill library in `~/.tink/skills/` (not an agent discovery root). Only skills explicitly promoted to `<project>/.agents/skills/` should be visible to agents.
- **Sub-Guide Discovery Gap (v0.2.0 Dogfood Finding):** Skills like `cro`, `landing-page`, `skill-scout`, and `threejs-shaders` bundle specialized sub-guides under `references/` (e.g. `references/form.md`, `references/experiments.md`). When `tink-route -i` only surfaces `SKILL.md`, agents must execute manual filesystem inspection (`ls`) to discover these critical domain guides.

## Proposed outcome
- A deterministic CLI utility, `tink-route`, installed at `~/.local/bin/tink-route`.
- **Bundled Reference & Script Surfacing:**
  - Upon installation (`-i`), `tink-route` scans the installed directory and surfaces `SKILL.md` plus any bundled references (`references/*.md`) and scripts (`scripts/*`).
  - Output:
    `Installed: .agents/skills/cro/SKILL.md`
    `References: references/experiments.md, references/form.md, references/saas.md`
  - Eliminates secondary `ls` probing and achieves true single-hop domain discovery.
- **Ephemeral Skill Tracking & Atomic Pruning:**
  - `tink-route -i` records installed skills in `<project>/.tink/ephemeral.json`.
  - `tink-route prune` sweeps all transient skills while preserving `.tink/skills.toml` and `manage-tink`.
- **Two-stage semantic gating powered by TypeSafe Jev (`jev-1.13.0`):**
  1. *Stage 1 (Need Gate):* Noul evaluation ($p < 0.60$ exits with code `1`, `no_skill_needed`).
  2. *Stage 2 (Candidate Selection):* Jev Choice evaluation with top candidate, runner-up, and margin.
- **Semantic Exit Codes:** `0` (routed/pruned), `1` (unrouted/no-op), `2` (error).

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
