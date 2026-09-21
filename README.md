# tink-route

> Dynamic, confidence-aware Agent Skill routing powered by [TypeSafe Jev](https://docs.typesafe.ai/) and [Tink](https://github.com/jon-devlapaz/tink).

Eliminates progressive disclosure prompt bloat by keeping skill libraries offline in `~/.tink/skills/` and dynamically loading only verified, load-bearing skills into `.agents/skills/` on demand.

---

## The Problem: Progressive Disclosure Bloat

The [Agent Skills standard](https://agentskills.io) injects every installed skill's name and description into the agent's base system prompt.
- With 40+ skills, this burns **2,500–5,000+ tokens on every single turn**, even for a one-line typo fix.
- Models suffer attention dilution, confusion between overlapping skill descriptions, and false-positive tool calls.

`tink-route` replaces progressive disclosure with a **two-stage semantic gate**:
1. **Stage 1 (Noul Gate):** *"Is a specialist skill strictly required for this task?"* If $p < 0.60$, exits in under 300 ms with `no_skill_needed`.
2. **Stage 2 (Choice Ranking):** Only if Stage 1 passes, Jev evaluates library candidates and selects the single most load-bearing skill.

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
Pass `--install` (`-i`) to automatically invoke `tink skill add <winner>`:

```bash
tink-route -i "Create WebGL particle simulation with custom GLSL shaders"
# Output:
# Recommended Skill: threejs-shaders (p=0.85, conf=0.85, noul=0.93)
# Installed into .agents/skills/threejs-shaders/
```

### 4. JSON Output (`--json`)
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
[ Cold Library ]      ~/.tink/skills/ (46+ skills, invisible to agent prompts)
                           │
                           ▼
[ Semantic Router ]   tink-route "<task>" [-i]
                        ├── Stage 1: Jev Noul Gate (p >= 0.60)
                        └── Stage 2: Jev Choice Ranking
                           │
                           ▼ (calls `tink skill add <winner>`)
[ Active Working Set] .agents/skills/ (0–2 skills active at a time)
```

---

## License

MIT © Jonathan De La Paz
