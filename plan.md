# Plan: Dynamic Agent Skill Routing v0.5.0 (Audit Remediation)

Remediating P1 and P2 boundary defects discovered in the 2026-09-21 System Sanity & Boundary Critique:
- **P1.1:** Prevent adoption and subsequent pruning of pre-existing manual installs (`cli.py`, `ephemeral.py`).
- **P1.2:** Implement fail-closed standard TOML parsing with stdlib `tomllib` (`ephemeral.py`).
- **P1.3:** Enforce exit code 2 on failed installs and suppress phantom `direct_read` activation (`cli.py`).
- **P2.1:** Harden ledger schema validation against nulls/dicts and catch missing `tink` executable (`ephemeral.py`, `cli.py`).
- **P2.2:** Preserve authentic `no_match` semantics and scores in multi-batch zero-survivor reduction (`client.py`).
- **P2.3:** Strip quotes from frontmatter values (`metadata.py`).
- **P2.4:** Normalize reference and script paths with `.as_posix()` (`cli.py`).
- **P2.5:** Ensure `prune --dry-run` exits 0 on clean inspection (`cli.py`).

## Files that change
- `src/tink_route/ephemeral.py`: stdlib `tomllib` parsing, fail-closed manifest validation, robust ledger schema typing (`list[str]`).
- `src/tink_route/cli.py`: pre-existing install detection, exit 2 on install failure, activation guard, POSIX path normalization, dry-run exit 0.
- `src/tink_route/client.py`: multi-batch zero-survivor `no_match` preservation, candidate validation.
- `src/tink_route/metadata.py`: YAML quote stripping on frontmatter name and description.
- `src/tink_route/__init__.py`: Bump `__version__ = "0.5.0"`.
- `pyproject.toml`: Bump version to `0.5.0`.
- `tests/test_tink_route.py`: Unit tests reproducing all 8 audit issues.
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

