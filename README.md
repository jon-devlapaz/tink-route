# tink-route

> Dynamic, confidence-aware Agent Skill routing powered by [TypeSafe Jev](https://docs.typesafe.ai/) and [Tink](https://github.com/jon-devlapaz/tink).

Eliminates progressive disclosure prompt bloat by keeping skill libraries offline in `~/.tink-library/skills/` (or `$TINK_HOME/skills`) and dynamically loading only verified, load-bearing skills into `.agents/skills/` on demand.

---

## The Problem: Progressive Disclosure Bloat

The [Agent Skills standard](https://agentskills.io) injects every installed skill's name and description into the agent's base system prompt.
- With 40+ skills, this burns **2,500–5,000+ tokens on every single turn**, even for a one-line typo fix.
- Models suffer attention dilution, confusion between overlapping skill descriptions, and false-positive tool calls.

`tink-route` replaces progressive disclosure with a **three-stage semantic gate** (all on by default; disable with `--no-tri-gate` / `--no-rerank`):
1. **Stage 1 (Tri-Noul Gate):** three orthogonal questions — does the task act on the user's system, would it follow a documented procedure, could prose alone suffice — averaged into one specialist score. If below threshold, exits in under 300 ms with `no_skill_needed`.
2. **Stage 2 (Choice Ranking):** Only if Stage 1 passes, Jev evaluates library candidates (batched, tournament-reduced past 24 skills) and selects the single most load-bearing skill.
3. **Stage 3 (Shortlist Rerank):** The top 3 candidates are re-read with `SKILL.md` body excerpts plus per-skill `fits` nouls, correcting lookalike winners; if no candidate fits, the result becomes `no_match`.

Pass `--multi` to return a ranked `candidates` list (`--top-k N`, default 3) instead of a single winner — every qualifying skill at or above threshold, drawn from all evaluated batches.

---

## Benchmark & Dogfood Results

Measured in isolated sandboxes (`tests/ab_eval.py`):

| Metric | Progressive Disclosure (Control) | `tink-route` (Treatment) |
| :--- | :--- | :--- |
| **Prompt Overhead / Turn** | **4,883 tokens** (constant tax) | **0 tokens** (standard coding turns) |
| **Routing Accuracy** | Variable (LLM hallucination risk) | **100.0% (6/6 passing)** |
| **False Positive Rate** | High on simple queries | **0.0% (0/3 on negative controls)** |
| **Mean Routing Latency** | N/A | **1,066 ms** |

---

## Installation

### With `pip` or `pipx`
```bash
pip install tink-route
# or isolated with pipx:
pipx install tink-route
```

### Local Development
```bash
git clone https://github.com/jon-devlapaz/tink-route.git
cd tink-route
pip install -e .
```

---

## Usage

### 1. Set API Key
```bash
export TYPESAFE_API_KEY="your_api_key_here"
```

### 2. Inspect / Recommend (Read-Only)
By default, `tink-route` respects Tink's separation of inspection vs. mutation authority:

```bash
# Standard coding query -> short-circuits at Stage 1 in ~300ms
tink-route "Fix off-by-one bug in binary search"
# Output:
# Status: no_skill_needed (specialist_noul: 0.07).
# Standard coding tools and models are sufficient.

# Specialized task -> routes to top candidate
tink-route "Create WebGL particle simulation with custom GLSL shaders"
# Output:
# Recommended Skill: threejs-shaders (p=0.85, conf=0.85, noul=0.93)
# To install run: tink skill add threejs-shaders
```

### 3. Atomic Install (`--install` / `-i`)
Pass `--install` (`-i`) to automatically invoke `tink skill add <winner>`. It surfaces `SKILL.md` plus any bundled reference guides or scripts in a single hop:

```bash
tink-route -i "Audit e-commerce checkout flow to optimize conversion rate"
# Output:
# Recommended Skill: cro (p=1.00, conf=0.99, noul=0.92)
# Installed: .agents/skills/cro/SKILL.md
# References: references/experiments.md, references/form.md
```

### 4. Post-Install Activation Contract
- When `tink-route -i` installs a skill into `.agents/skills/<name>/`, modern agent harnesses (Pi, Cursor, Claude Code, Codex) do **not require a session restart**.
- The invoking agent immediately reads the emitted `entrypoint` path (`.agents/skills/<name>/SKILL.md`) in a single hop.
- The `--json` payload includes an explicit `activation` contract block:
  ```json
  "activation": {
    "mode": "direct_read",
    "entrypoint": ".agents/skills/cro/SKILL.md",
    "references": ["references/form.md", "references/experiments.md"],
    "scripts": [],
    "restart_required": false,
    "instruction": "Read SKILL.md directly; mid-session use does not require session restart."
  }
  ```
- `activation.mode: "direct_read"` is emitted only when installation and ephemeral ownership tracking both succeed. If installation succeeds but ledger tracking fails, the result keeps `installed: true`, reports `tracking_error`, omits activation, and exits `2`.

### 5. Exit Code Contract
Designed for clean scripting and deterministic agent branching:
- **`0`**: Route succeeded (skill identified, and installed if `-i` was passed); or `prune` completed (including nothing to prune), or `prune --dry-run` completed cleanly.
- **`1`**: Unrouted (`no_skill_needed`, `no_match`, `no_candidates_available`, or `uncertain` below threshold).
- **`2`**: Operational error (missing `TYPESAFE_API_KEY`, library directory not found, failed `tink` installation, ownership ledger failure, malformed `.tink/skills.toml`, or missing `tink` binary).

```bash
# Example shell branching:
if tink-route -i "$TASK"; then
    echo "Specialist skill installed and ready."
elif [ $? -eq 1 ]; then
    echo "No specialist skill needed; using standard tools."
else
    echo "Operational error occurred." >&2
fi
```

### 6. Ownership-Safe Milestone Pruning (`tink-route prune`)
Eliminates manual cleanup bookkeeping while strictly preserving skills owned by other tools:

```bash
# Preview what would be removed (ledger-only by default):
tink-route prune --dry-run
# Output: Eligible for pruning (1): threejs-shaders

# Sweep only skills installed and tracked by tink-route:
tink-route prune
# Output:
# Pruned 1 ephemeral skill(s): threejs-shaders
# Clean state confirmed in .agents/skills/.
```

- **Ownership Safety:** Only skills installed and recorded by `tink-route -i` are pruned by default. If a skill was already installed manually prior to routing, it is **never adopted** into `.tink/ephemeral.json` and will not be pruned.
- **Fail-Closed Manifest Protection:** Skills declared in `.tink/skills.toml` (parsed with standard `tomllib`) and reserved skills (`manage-tink`) are **strictly protected** and never pruned in any mode. If `.tink/skills.toml` has invalid syntax, pruning fails closed with exit code `2`.
- **Broad Sweep (`--all-unpinned`):** To sweep all unpinned skills in `.agents/skills/`, pass `tink-route prune --all-unpinned`. Warning: with no `.tink` ledger or manifest present, every installed skill looks unpinned — the CLI prints a stderr warning in that case.
- **Pass `--no-ephemeral`:** To install a permanent skill without ephemeral tracking: `tink-route -i --no-ephemeral "<task>"`.

### 7. JSON Output (`--json`)
For programmatic invocation by AI agents:

```bash
tink-route --json "Audit this codebase architecture"
```
```json
{
  "status": "routed",
  "task": "Audit this codebase architecture",
  "winner": "improve-codebase-architecture",
  "probability": 0.93,
  "confidence": 0.91,
  "specialist_noul": 0.97,
  "threshold": 0.6,
  "elapsed_ms": 1205
}
```

---

## System Architecture

```
[ Cold Library ]      ~/.tink-library/skills/ (46+ skills, invisible to agent prompts)
                           │
                           ▼
[ Semantic Router ]   tink-route "<task>" [-i]
                        ├── Stage 1: Tri-Noul Gate (p >= 0.60)
                        ├── Stage 2: Jev Choice Ranking
                        └── Stage 3: Shortlist Rerank (fits nouls) [--multi: ranked candidates]
                           │
                           ▼ (calls `tink skill add <winner>`)
[ Active Working Set] .agents/skills/ (0–2 skills active at a time)
```

---

## License

MIT © Jonathan De La Paz
