# Plan: Dynamic Agent Skill Routing v0.5.1 (Concurrency & Validation Hardening)

Remediating boundary defects from the secondary audit:
- **1. Concurrent Ledger Writes:** Add `fcntl.flock` on `.tink/ephemeral.lock` and atomic file write (`os.replace`) in `ephemeral.py`.
- **2. Partial Prune Error Exit:** Return exit `2` whenever `res.get("errors")` is non-empty in `cli.py`.
- **3. Protected Ledger Writes:** Catch `Exception` in `record_ephemeral_skill` in `cli.py`, reporting clean error and exit `2`.
- **4. API Candidate Validation:** Strictly validate `winner` in `client.py` against candidate set and safe identifier syntax (rejecting `../outside` with `RuntimeError`).

## Files that change
- `src/tink_route/ephemeral.py`: Ledger file locking and atomic temporary file rename.
- `src/tink_route/cli.py`: Partial prune exit 2 check, protected `record_ephemeral_skill` error handling.
- `src/tink_route/client.py`: Candidate membership validation and path traversal rejection.
- `src/tink_route/__init__.py`: Bump `__version__ = "0.5.1"`.
- `pyproject.toml`: Bump version to `0.5.1`.
- `tests/test_tink_route.py`: Unit tests reproducing all 4 edge cases.
- `README.md`: Document updated contracts and behaviors.

## Implementation Steps

1. **Step 1: Ephemeral Ledger & TOML Manifest Hardening (`ephemeral.py`)**
   - Import `tomllib` (Python 3.11+ stdlib).
   - In `load_pinned_skills(project_dir)`: parse with `tomllib.loads()`. If syntax error, raise `ValueError`.
   - In `load_ephemeral_skills(project_dir)`: safely extract string list.
   - In `prune_ephemeral_skills(project_dir)`: handle manifest syntax errors fail-closed.

2. **Step 2: CLI Pre-Existing Install & Activation Guard (`cli.py`)**
   - In `install_skill()`: check `(cwd / ".agents" / "skills" / skill_name / "SKILL.md").is_file()` *before* invoking `tink skill add`.
   - Record in `ephemeral.json` ONLY if it was NOT pre-existing.
   - If `res.returncode != 0`: return `success: False`.
   - In `main()`: if `args.install` fails, exit `2` and omit `activation`.
   - If not `args.install`: omit `activation` block.
   - Use `.as_posix()` for relative reference/script paths.
   - If `args.dry_run`: exit `0`.

3. **Step 3: Multi-Batch Reduction & Sentinel Preservation (`client.py`)**
   - When all batches return `NO_MATCH_SENTINEL`: winner is `NO_MATCH_SENTINEL`, status is `no_match`.
   - Preserve batch's authentic confidence/probabilities.

4. **Step 4: Metadata Parsing Quote Stripping (`metadata.py`)**
   - Strip leading/trailing double and single quotes from `name` and `description`.

5. **Step 5: Version Bump & Comprehensive Unit Tests (`tests/test_tink_route.py`)**
   - Bump version to `0.5.0`.
   - Add unit tests for every audited case.
   - Verify 100% test pass rate.

6. **Step 6: Live Reproduction & Deployment**
   - Verify on local reproductions in `/tmp/tink-route-audit`.
   - Commit, push, CI verify, and release `v0.5.0`.

