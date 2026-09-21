# Plan: Dynamic Agent Skill Routing v0.2.0 Ephemeral & Prune Engine (from `spec.md` 2026-09-21)

## Files that change
- `src/tink_route/ephemeral.py`: New module managing `.tink/ephemeral.json` ledger, reading `.tink/skills.toml`, and pruning unpinned skills.
- `src/tink_route/cli.py`: Add `prune` command (and `--prune` alias), `--ephemeral` flag on install, and wire into `ephemeral.py`.
- `src/tink_route/__init__.py`: Bump `__version__ = "0.2.0"`.
- `pyproject.toml`: Bump version to `0.2.0`.
- `tests/test_tink_route.py`: Tests for ephemeral recording, manifest protection, and prune execution.
- `README.md`: Document `tink-route prune` and ephemeral lifecycle.

## Order of work

1. **Test-First Scaffold:**
   - Author tests in `tests/test_tink_route.py`:
     - Test adding a skill to `.tink/ephemeral.json`.
     - Test `prune_ephemeral_skills()` removes ephemeral skills via `tink skill remove`.
     - Test `prune_ephemeral_skills()` preserves `manage-tink` and skills declared in `.tink/skills.toml`.
     - Test `tink-route prune --dry-run` reports candidates without running removal.
     - Test CLI dispatch to `prune`.

2. **Implement `src/tink_route/ephemeral.py`:**
   - Functions: `record_ephemeral_skill(project_dir, skill_name)`, `load_ephemeral_skills(project_dir)`, `load_pinned_skills(project_dir)`, `prune_ephemeral_skills(project_dir, dry_run=False)`.
   - Protects `manage-tink` and pinned skills.
   - Cleans up `.tink/ephemeral.json` after successful removals.

3. **Wire into `cli.py`:**
   - Add positional `subcommand` or flag check: if first arg is `"prune"`, dispatch to prune handler.
   - On `tink-route -i`, automatically record installed skill in `.tink/ephemeral.json` unless `--no-ephemeral` is supplied.
   - Version bump to `0.2.0`.

4. **Verify Locally Against Test Suite:**
   - Run `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
   - Ensure 100% pass rate.

5. **Deploy & Release (Stage 5):**
   - Commit changes with semantic commit message.
   - Push to GitHub `main`.
   - Verify GitHub Actions CI run.
   - Create GitHub release `v0.2.0`.

## Risks
- Corrupting or over-pruning pinned skills:
  - *Mitigation:* Explicit protection: if `.tink/skills.toml` contains `name = "..."`, or if name is `manage-tink`, never remove it.
  - Provide `--dry-run` flag so users and agents can preview.

## Proof
- All unit tests pass in `tests/test_tink_route.py`.
- `tink-route -i "<task>"` records in `.tink/ephemeral.json`.
- `tink-route prune` removes the skill and restores clean `.agents/skills/`.
- GitHub Actions CI workflow passes on Python 3.11, 3.12, 3.13.
