# Plan: Dynamic Agent Skill Routing v0.4.0 (from `spec.md` 2026-09-21)

Addressing GitHub Issues:
- Issue #1: Ownership-safe pruning (ledger-only by default, `--all-unpinned` opt-in).
- Issue #2: Mid-session skill activation contract & payload.
- Issue #3: Preserve authentic Jev confidence semantics during batched routing.

## Files that change
- `src/tink_route/ephemeral.py`: Ledger-only pruning default; `--all-unpinned` parameter.
- `src/tink_route/client.py`: Retain authentic Jev confidence/probability in batched single-winner and zero-winner paths.
- `src/tink_route/cli.py`: Add `--all-unpinned` flag; populate `activation` metadata block.
- `src/tink_route/__init__.py`: Bump `__version__ = "0.4.0"`.
- `pyproject.toml`: Bump version to `0.4.0`.
- `tests/test_tink_route.py`: Comprehensive tests for Issues #1, #2, #3.
- `README.md`: Document activation contract, ledger-only prune, and `--all-unpinned`.

## Order of work

1. **Test-First Implementation:**
   - Author tests in `tests/test_tink_route.py`:
     - Test that a manually installed skill (not in `.tink/ephemeral.json`) is **preserved** by default `prune`.
     - Test that `--all-unpinned` prunes unpinned skills while preserving `.tink/skills.toml`.
     - Test batched Stage 2 with `> 24` skills where single winner preserves Jev-reported `0.77` confidence (not `0.85`).
     - Test batched Stage 2 where zero winners returns `no_skill_needed` without hardcoded `0.90`.
     - Test that `--json` output contains the `activation` contract block.

2. **Implement Ephemeral Ledger-Only Pruning (`ephemeral.py`):**
   - Update `prune_ephemeral_skills(project_dir, dry_run=False, all_unpinned=False)`.
   - Default: `candidates = ephemeral_tracked & installed`.
   - If `all_unpinned`: `candidates = (ephemeral_tracked & installed) | {s for s in installed if s not in pinned}`.
   - Preserves `manage-tink` and pinned skills.

3. **Implement Authentic Batched Confidence (`client.py`):**
   - In `route()`: when `len(skills) > BATCH_SIZE`:
     - Store `{"name": winner, "confidence": conf, "probabilities": probs}` for each batch winner.
     - When `len(batch_winners) == 1`: use the authentic `confidence` and `probabilities` directly from that batch.
     - When `len(batch_winners) == 0`: use Jev-derived probability (no hardcoded `0.90`).

4. **Implement Activation Contract in CLI (`cli.py`):**
   - Add `activation` dictionary on routed install:
     `{"mode": "direct_read", "entrypoint": skill_path, "references": references, "restart_required": False}`.
   - Wire `--all-unpinned` into prune command.
   - Version bump to `0.4.0`.

5. **Verify Locally Against Test Suite:**
   - Run `PYTHONPATH=src python3 -m unittest discover -s tests -v`.
   - Ensure 100% pass rate.

6. **Deploy & Close Issues (Stage 5):**
   - Commit changes, push to GitHub `main`.
   - Verify GitHub Actions CI run.
   - Release `v0.4.0`.
   - Close GitHub Issues #1, #2, #3 with references to the commits.

## Proof
- All unit tests pass in `tests/test_tink_route.py`.
- No synthetic `0.85` or `0.90` strings exist in `client.py`.
- `tink-route prune` does not touch manually installed skills.
- GitHub Actions CI workflow passes on Python 3.11, 3.12, 3.13.
