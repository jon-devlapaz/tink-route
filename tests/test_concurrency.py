"""Tests for concurrency, reentrancy, atomic writes, and ghost skill hygiene."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tink_route.adapters.ledger import FilesystemLedger


class TestConcurrencyAndLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = FilesystemLedger()

    def test_conc_4_and_5_atomic_replacement_cleans_up_orphaned_tmp(self) -> None:
        """CONC-4 & CONC-5: fsync and cleanup on write failure so no tmp files leak."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            tink_dir = tmppath / ".tink"
            tink_dir.mkdir()

            # Simulate failure during os.replace
            with patch("os.replace", side_effect=OSError("Disk write error")):
                with self.assertRaises(OSError):
                    self.ledger.record_ephemeral_skill(tmppath, "test-skill")

            # Check that no ephemeral.json.tmp.* files remain
            tmp_files = list(tink_dir.glob("ephemeral.json.tmp.*"))
            self.assertEqual(tmp_files, [], "Orphaned temporary files must not leak on failure")

    def test_conc_6_ghost_skill_cleanup_in_prune(self) -> None:
        """CONC-6: Tracked skills absent from disk are purged from ephemeral.json on prune."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            skills_dir = tmppath / ".agents" / "skills"
            skills_dir.mkdir(parents=True)

            # 1. A live installed skill on disk
            (skills_dir / "live-skill").mkdir()
            (skills_dir / "live-skill" / "SKILL.md").write_text("live")

            # 2. Record both live-skill and ghost-skill (never created on disk) in ledger
            self.ledger.record_ephemeral_skill(tmppath, "live-skill")
            self.ledger.record_ephemeral_skill(tmppath, "ghost-skill")

            self.assertEqual(
                sorted(self.ledger.load_ephemeral_skills(tmppath)),
                ["ghost-skill", "live-skill"],
            )

            # Prune live-skill
            mock_executor = MagicMock()
            mock_executor.run.return_value = (0, "Removed live-skill", "")

            report = self.ledger.prune(tmppath, dry_run=False, executor=mock_executor)

            self.assertEqual(report.pruned, ["live-skill"])
            # Ghost skill was not on disk, so it wasn't prunable via tink skill remove,
            # but it MUST be removed from ephemeral.json!
            remaining = self.ledger.load_ephemeral_skills(tmppath)
            self.assertEqual(remaining, [], "Ghost skills must be purged from ledger")


if __name__ == "__main__":
    unittest.main()
