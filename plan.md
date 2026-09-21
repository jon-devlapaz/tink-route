# Plan: Dynamic Agent Skill Routing via `tink-route` (from `spec.md` 2026-09-21)

## Files that change
- `/Users/jondev/.local/bin/tink-route` (new executable Python script)
- `/Users/jondev/tests/test_tink_route.py` (new test suite covering CLI parsing, Jev payload construction, Stage 1 gating, Stage 2 ranking, and `--install` hand-off)
- `~/.pi/agent/skills/` (cleanup: preserve `background-terminals` & `subagents` into `~/.tink/skills/`, remove from `~/.pi/agent/skills/`)

## Order of work

1. **Test-Driven Foundation (Stage 4 prep):**
   - Author `/Users/jondev/tests/test_tink_route.py` using Python's `unittest` standard library.
   - Mock TypeSafe HTTP responses to test:
     - Argument parsing (`-i`, `--install`, `--json`, `--threshold`, `--library`).
     - Stage 1 Noul short-circuit: When Noul $< 0.55$, exits with `no_skill_needed` without calling Stage 2.
     - Stage 2 Choice selection: When Noul $\ge 0.55$, routes to top candidate.
     - `--install` behavior: Calls `tink skill add <winner>` when flag is passed.

2. **Implement Core Engine (`/Users/jondev/.local/bin/tink-route`):**
   - Standard Python 3.11+ zero-dependency script (`urllib.request`, `json`, `os`, `sys`, `argparse`, `pathlib`).
   - Read and parse skill library from `~/.tink/skills/*/SKILL.md` (extract frontmatter `name` and `description`).
   - Implement TypeSafe client calling `https://api.typesafe.ai/v1/evaluate` with `TYPESAFE_API_KEY`.
   - Implement two-stage evaluation logic.
   - Implement subprocess hand-off to `tink skill add` when `-i` / `--install` is present.
   - Make executable (`chmod +x ~/.local/bin/tink-route`).

3. **Verify Locally Against Test Suite:**
   - Run `python3 -m unittest discover -s /Users/jondev/tests -v`.
   - Ensure 100% test pass rate before running live network probes.

4. **Live Validation on Real Queries:**
   - Test 1 (Standard coding task): `tink-route "Fix the off-by-one error in array sorting"` $\to$ Expected: `no_skill_needed`.
   - Test 2 (Specialized domain task): `tink-route "Create a ThreeJS particle vortex with custom GLSL shaders"` $\to$ Expected: Routes to `threejs-shaders`.
   - Test 3 (Atomic Install verification): Run with `--install` on a test task and verify receipt in `.agents/skills/`.

5. **Pi Environment Alignment & Cleanup:**
   - Copy `background-terminals` and `subagents` from `~/.pi/agent/skills/` into `~/.tink/skills/` if not present.
   - Clean `~/.pi/agent/skills/` so Pi system prompts start completely free of progressive disclosure bloat.

## Risks

- **TypeSafe API Rate Limits or Socket Timeouts:**
  - *Mitigation:* Explicit 5-second socket timeout; graceful JSON error exit with code `2`.
- **Large Candidate Payloads:**
  - *Mitigation:* Filter candidates to those with valid non-empty descriptions; truncate descriptions to 250 characters if library exceeds 60 skills.
- **Accidental Workspace Mutation:**
  - *Mitigation:* Require explicit `--install` flag; default is strictly read-only inspection.

## Proof

- `python3 -m unittest discover -s /Users/jondev/tests -v` exits 0 with all green tests.
- Live dry-run `tink-route --json "Write a quicksort in Python"` returns `{"status": "no_skill_needed"}`.
- Live dry-run `tink-route --json "Audit this codebase architecture"` returns `{"status": "routed", "winner": "improve-codebase-architecture"}`.
- `~/.pi/agent/skills/` is clean.
