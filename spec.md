# Spec: Dynamic Agent Skill Routing (`tink-route`)

Author: Claude & jondev. Status: approved.
Reference: `intent.md` (2026-09-21)

---

## 1. Overview & Objectives

`tink-route` is a deterministic command-line interface designed to bridge the gap between Tink's cold skill library (`~/.tink/skills/`) and the active project working set (`.agents/skills/`). It uses TypeSafe Jev (`jev-1.13.0`) to perform typed, confidence-aware routing, ensuring that:
1. Standard coding tasks incur zero skill installations and avoid unnecessary catalog evaluations.
2. Specialized tasks are mapped to the most load-bearing skill in the library.
3. The agent system prompt remains free of progressive disclosure token bloat.

---

## 2. Functional Requirements

### 2.1 CLI Interface
- **Executable path:** `~/.local/bin/tink-route`
- **Routing Usage:** `tink-route "<request>" [options]`
- **Pruning Usage:** `tink-route prune [options]`
- **Options:**
  - `-i`, `--install`: Automatically execute `tink skill add <winner>` upon a qualifying route decision.
  - `--ephemeral / --no-ephemeral`: Mark skill as ephemeral in `.tink/ephemeral.json` for automatic pruning (default: `true` when `-i` is used).
  - `--threshold <float>`: Minimum probability threshold for the Stage 1 need gate and Stage 2 selection (default: `0.60`).
  - `--json`: Output full routing diagnostics and decision in machine-readable JSON.
  - `--library <path>`: Directory containing skill candidate trees (default: `~/.tink/skills`).
  - `--model <name>`: Pinned Jev model identifier (default: `jev-1.13.0`).
  - `--dry-run`: Used with `prune` to preview which skills would be removed without modifying `.agents/skills/`.
  - `-v`, `--version`: Output installed version.
  - `-h`, `--help`: Display usage information.

### 2.2 Ephemeral Ledger & Pruning Subcommand
- **Storage:** Stored in `<project>/.tink/ephemeral.json`:
  ```json
  {
    "version": 1,
    "skills": ["threejs-shaders"]
  }
  ```
- **Ownership-Safe Pruning Algorithm (`tink-route prune`):**
  1. Read `.tink/ephemeral.json` if it exists (`ephemeral_tracked`).
  2. Read `.tink/skills.toml` if it exists (`pinned`).
  3. Formulate the set of pruning candidates:
     - **Default Mode (Ledger-Only):** `candidates = ephemeral_tracked & installed`.
       *Only skills installed and recorded by `tink-route -i` are eligible.*
       Manual `tink skill add` or other harness installs (Cursor, Claude) are strictly preserved.
     - **Broad Sweep Mode (`--all-unpinned`):** `candidates = (ephemeral_tracked & installed) | {s for s in installed if s not in pinned}`.
     - **Invariants:** Never prune `manage-tink`. Never prune skills explicitly declared in `.tink/skills.toml`.
  4. If `--dry-run`:
     - Print eligible skills and exit `0`.
  5. For each qualifying skill:
     - Invoke `tink skill remove <name>`.
     - Remove skill from `.tink/ephemeral.json`.
  6. Output count and list of removed skills:
     `Pruned 1 ephemeral skill(s): threejs-shaders`
     `Clean state confirmed in .agents/skills/.`
  7. Exit code: `0` if skills were pruned, `1` if no ephemeral skills existed to prune.

### 2.2 Stage 1: Specialist Need Gate (Noul)
- Before comparing any library skills, the utility constructs a Jev `noul` question evaluating whether an external skill is required.
- **Evaluation prompt:**
  `"Does this task strictly require a specialized domain skill, workflow, or institutional guide outside standard programming tools, reasoning, and shell utilities?"`
- **Rule:**
  - If $p(\text{specialist\_needed}) < \text{threshold}$ (default: `0.55`):
    - Return `status: "no_skill_needed"`.
    - Terminate immediately without sending candidate lists to the API.

### 2.3 Stage 2: Candidate Ranking & Selection (Choice)
- If Stage 1 passes ($p \ge \text{threshold}$):
  - Ingest `SKILL.md` frontmatter (`name`, `description`) across all subdirectories in `~/.tink/skills/`.
  - Construct a Jev `choice` question:
    - `criteria`: Map of skill names to their published descriptions.
    - `instructions`: `"Which skill is most directly load-bearing and capable of executing the requested transformation?"`
  - **Batched Reduction & Confidence Preservation:**
    - When candidate count $> \text{BATCH_SIZE}$ (24):
      - Split into deterministic chunks of $\le \text{BATCH_SIZE}$.
      - For each batch, record Jev's authentic `choice`, `confidence`, and `probabilities`.
      - Exclude sentinels (`__no_skill__`, `__no_match__`) from survivor candidate lists.
      - If multiple batch winners survive: evaluate final Choice reduction across survivors with Jev.
      - If exactly one batch winner survives: **preserve its authentic Jev `confidence` and `probabilities`** from that batch (no synthetic constants).
      - If zero batch winners survive: winner is `__no_skill__` with Jev-derived probability.
- **Rule:**
  - Top candidate must meet or exceed `threshold`.
  - If top probability $< \text{threshold}$, return `status: "uncertain"` / `status: "review"`.
  - Otherwise return `status: "routed"` with the selected skill.

### 2.4 Mid-Session Skill Activation Contract
- **The Contract:**
  1. `tink-route -i` performs filesystem installation via `tink skill add`.
  2. In modern agent harnesses (Pi, Claude Code, Cursor, Codex), newly installed skills are **immediately active without session restart**.
  3. The agent must read `.agents/skills/<winner>/SKILL.md` using its standard `read()` tool call in a single hop.
  4. The `--json` payload includes an explicit `activation` block providing canonical instructions:
     ```json
     "activation": {
       "mode": "direct_read",
       "entrypoint": ".agents/skills/cro/SKILL.md",
       "references": ["references/form.md", "references/experiments.md"],
       "restart_required": false,
       "instruction": "Read SKILL.md directly; mid-session use does not require session restart."
     }
     ```

### 2.4 Mutation & Execution Hand-off
- When `status == "routed"` and `--install` is supplied:
  - Verify that the target project has an initialized `.agents/` or create it if missing via `tink skill add`.
  - Execute `tink skill add <winner>`.
  - Scan `.agents/skills/<winner>/` for bundled assets:
    - `skill_path`: `.agents/skills/<winner>/SKILL.md`
    - `references`: relative paths of files in `.agents/skills/<winner>/references/`
    - `scripts`: relative paths of files in `.agents/skills/<winner>/scripts/`
  - Output format:
    `Installed: .agents/skills/<winner>/SKILL.md`
    `References: references/experiments.md, references/form.md, references/saas.md` (if references exist)
    `Scripts: scripts/audit.sh` (if scripts exist)
  - Exit code: `0`.
- When `status == "routed"` and `--install` is not supplied:
  - Output winner, confidence, probability, and instructions to install:
    `Recommended Skill: <winner> (p=0.85, conf=0.85, noul=0.93)`
    `To install run: tink skill add <winner>`
  - Exit code: `0`.
- When `status == "no_skill_needed"`:
  - Output:
    `Status: no_skill_needed (specialist_noul: 0.12). Standard coding tools and models are sufficient.`
  - Exit code: `1`.
- When `status == "uncertain"`:
  - Output top candidate, runner up, and margin:
    `Status: uncertain. Top candidate '<top>' (p=0.59) fell below threshold 0.60. Runner-up: '<second>' (p=0.35, margin=0.24).`
  - Exit code: `1`.

---

## 3. Non-Functional Requirements

- **Runtime:** Python 3.11+ using standard library HTTP (`urllib.request`) and JSON parsing, requiring zero external package installations.
- **Latency Budget:**
  - Stage 1 alone: $\le 400\text{ ms}$.
  - Full two-stage route: $\le 1,400\text{ ms}$.
- **Security & Credentials:**
  - Read `TYPESAFE_API_KEY` from process environment.
  - Never log, echo, or serialize the key in error outputs or payloads.
  - Do not upload file contents or project secrets; only the task description and public skill metadata are transmitted.
- **Exit Codes Contract:**
  - `0`: Route succeeded and skill identified (or installed).
  - `1`: Unrouted (no skill needed, no match, or uncertain below threshold).
  - `2`: Operational failure (missing API key, network timeout, invalid JSON, or missing library).

---

## 4. Data Schemas

### 4.1 TypeSafe API Payload
```json
{
  "model": "jev-1.13.0",
  "state": {
    "task": "<user_task>",
    "candidates": {
      "<skill_name>": "<description>"
    }
  },
  "questions": {
    "specialist_needed": {
      "type": "noul",
      "instructions": "Does this task strictly require a specialized domain skill..."
    },
    "selected_skill": {
      "type": "choice",
      "instructions": "Which skill is most directly load-bearing...",
      "criteria": {
        "<skill_name>": "<description>"
      }
    }
  }
}
```

### 4.2 CLI JSON Output (`--json`)
```json
{
  "status": "routed",
  "task": "Build a ThreeJS particle vortex shader",
  "winner": "threejs-shaders",
  "probability": 0.94,
  "confidence": 0.89,
  "specialist_noul": 0.96,
  "installed": true,
  "elapsed_ms": 1120
}
```

---

## 5. Areas of Concern & Mitigations

1. **Network or Provider Latency / Outages:**
   - *Concern:* If the TypeSafe API times out or is unreachable, the agent loop could hang.
   - *Mitigation:* Set a strict 5-second socket timeout on all HTTP requests. On timeout or 5xx, exit cleanly with code `2` and fallback advice.
2. **Library Scalability & Batching:**
   - *Concern:* As `~/.tink/skills/` grows past 50–100 skills, single Choice requests could exceed byte/token budgets.
   - *Mitigation:* Batch candidates into chunks of $\le 20$ candidates per request if needed; for the current 46 skills, two parallel batches or high-signal keyword pre-filtering ensure safe byte margins (< 16 KB).
3. **Accidental File Overwrites in `.agents/skills/`:**
   - *Concern:* Running `tink skill add` might overwrite an existing customized skill.
   - *Mitigation:* Delegate the actual add to `tink skill add`, which natively enforces divergence and receipt checks.
