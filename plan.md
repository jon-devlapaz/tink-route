# Plan: Dynamic Agent Skill Routing v0.1.1 Enhancements (from `spec.md` 2026-09-21)

## Files that change
- `src/tink_route/client.py`: Return runner-up candidate and margin when `status == "uncertain"`.
- `src/tink_route/cli.py`: Emit exact `.agents/skills/<winner>/SKILL.md` path on install; apply exit code contract (0=routed, 1=unrouted/no-op, 2=error).
- `src/tink_route/__init__.py`: Bump `__version__ = "0.1.1"`.
- `pyproject.toml`: Bump version to `0.1.1`.
- `tests/test_tink_route.py`: Unit tests asserting exit code contract and exact SKILL.md path emission.
- `README.md`: Update usage examples and exit code documentation.

## Order of work

1. **Test-First Updates:**
   - Update `tests/test_tink_route.py` with test cases verifying:
     - Return code `0` when `status == "routed"`.
     - Return code `1` when `status == "no_skill_needed"` or `"uncertain"`.
     - Output contains `.agents/skills/<winner>/SKILL.md` on successful install.
     - Output contains runner-up name and margin on uncertain decisions.

2. **Implement Core Enhancements:**
   - `client.py`: Sort probabilities to extract `top_candidate`, `runner_up`, and `margin`.
   - `cli.py`: Update human-readable output to print `Installed: .agents/skills/{winner}/SKILL.md`. Update `main()` return value according to exit code contract.
   - Version bump to `0.1.1`.

3. **Verify Locally Against Test Suite:**
   - Run `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
   - Ensure 100% pass rate.

4. **Verify Live CLI Execution:**
   - Probe standard coding query $\to$ verify exit code `1`.
   - Probe specialist query with `-i` $\to$ verify `Installed: .agents/skills/.../SKILL.md` and exit code `0`.
   - Probe ambiguous query $\to$ verify runner-up explanation and exit code `1`.

5. **Deploy & Release (Stage 5):**
   - Commit changes with semantic commit message.
   - Push to GitHub `main`.
   - Verify GitHub Actions CI run.
   - Create GitHub release `v0.1.1`.

## Risks
- Shell scripts expecting exit code `0` for `no_skill_needed`:
  - *Mitigation:* Document clearly in README. Standard Unix semantics treat non-matching search/routing as exit 1 (matching `grep`, `diff`, `test`). `--json` output remains backwards compatible.

## Proof
- All unit tests pass in `tests/test_tink_route.py`.
- `tink-route "Fix typo"; echo $?` prints `1`.
- `tink-route "Create GLSL shader"; echo $?` prints `0`.
- GitHub Actions CI workflow passes on all 3 Python versions.
