# Plan: Dynamic Agent Skill Routing v0.3.0 Bundled Assets Surfacing (from `spec.md` 2026-09-21)

## Files that change
- `src/tink_route/cli.py`: Scan installed skill directory to extract and surface bundled `references/` and `scripts/`.
- `src/tink_route/__init__.py`: Bump `__version__ = "0.3.0"`.
- `pyproject.toml`: Bump version to `0.3.0`.
- `tests/test_tink_route.py`: Tests for bundled asset discovery and emission.
- `README.md`: Update documentation with references & scripts output.

## Order of work

1. **Test-First Scaffold:**
   - Author test in `tests/test_tink_route.py` with mock filesystem containing `references/` and `scripts/`.
   - Assert `install_skill` returns `references` and `scripts` lists.

2. **Implement Asset Scanning in `src/tink_route/cli.py`:**
   - Inspect `.agents/skills/<name>/references/` and `.agents/skills/<name>/scripts/`.
   - Add to result payload and emit in CLI output.
   - Version bump to `0.3.0`.

3. **Verify Locally Against Test Suite:**
   - Run `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
   - Ensure 100% pass rate.

4. **Deploy & Release (Stage 5):**
   - Commit changes with semantic commit message.
   - Push to GitHub `main`.
   - Verify GitHub Actions CI run.
   - Create GitHub release `v0.3.0`.

5. **Dogfood & Loop:**
   - Run in herdr sandbox and collect agent feedback until only nitpicks remain.

## Proof
- All unit tests pass in `tests/test_tink_route.py`.
- `tink-route -i "e-commerce checkout CRO"` outputs `Installed: .../SKILL.md` and `References: ...`.
- GitHub Actions CI passes on Python 3.11, 3.12, 3.13.
