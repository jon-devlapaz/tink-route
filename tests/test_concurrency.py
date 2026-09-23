"""Tests for concurrency, reentrancy, atomic writes, and ghost skill hygiene."""

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from tink_route.adapters.ledger import FilesystemLedger, _PROJECT_LOCKS
from tink_route.core.models import PruneReport


class TestConcurrencyAndLedger(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = FilesystemLedger()

    def test_conc_1_reentrant_lock_single_os_flock(self) -> None:
        """CONC-1: fcntl.flock(LOCK_EX) only called on depth 0, LOCK_UN on depth 1 exit."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            lock_calls: list[tuple[str, int]] = []

            mock_fcntl = MagicMock()

            def fake_flock(fd: int, op: int) -> None:
                if op == mock_fcntl.LOCK_EX:
                    lock_calls.append(("EX", fd))
                elif op == mock_fcntl.LOCK_UN:
                    lock_calls.append(("UN", fd))

            mock_fcntl.flock.side_effect = fake_flock
            mock_fcntl.LOCK_EX = 2
            mock_fcntl.LOCK_UN = 8

            with patch("tink_route.adapters.ledger.fcntl", mock_fcntl), patch("tink_route.adapters.ledger.msvcrt", None):
                # Outer lock (depth 0 -> 1)
                with self.ledger.lock(tmppath):
                    self.assertEqual(len(lock_calls), 1)
                    self.assertEqual(lock_calls[0][0], "EX")
                    proj_key = tmppath.resolve()
                    self.assertIn(proj_key, _PROJECT_LOCKS)
                    self.assertEqual(_PROJECT_LOCKS[proj_key][1], 1)

                    # Inner lock 1 (depth 1 -> 2)
                    with self.ledger.lock(tmppath):
                        self.assertEqual(len(lock_calls), 1)  # No additional OS flock
                        self.assertEqual(_PROJECT_LOCKS[proj_key][1], 2)

                        # Inner lock 2 (depth 2 -> 3)
                        with self.ledger.lock(tmppath):
                            self.assertEqual(len(lock_calls), 1)  # Still only 1 OS flock
                            self.assertEqual(_PROJECT_LOCKS[proj_key][1], 3)

                        self.assertEqual(len(lock_calls), 1)  # No unlock yet
                        self.assertEqual(_PROJECT_LOCKS[proj_key][1], 2)

                    self.assertEqual(len(lock_calls), 1)  # No unlock yet
                    self.assertEqual(_PROJECT_LOCKS[proj_key][1], 1)

                # Outermost lock exited (depth 1 -> 0): OS flock UN called, removed from registry
                self.assertEqual(len(lock_calls), 2)
                self.assertEqual(lock_calls[1][0], "UN")
                self.assertNotIn(proj_key, _PROJECT_LOCKS)

    def test_conc_2_strict_lifo_order(self) -> None:
        """CONC-2: Thread lock acquired before OS lock; OS lock released before thread lock."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            events: list[str] = []

            mock_fcntl = MagicMock()
            mock_fcntl.LOCK_EX = 2
            mock_fcntl.LOCK_UN = 8

            def fake_flock(fd: int, op: int) -> None:
                if op == mock_fcntl.LOCK_EX:
                    events.append("os_lock_acquired")
                elif op == mock_fcntl.LOCK_UN:
                    events.append("os_lock_released")

            mock_fcntl.flock.side_effect = fake_flock

            with patch("tink_route.adapters.ledger.fcntl", mock_fcntl), patch("tink_route.adapters.ledger.msvcrt", None):
                proj_key = tmppath.resolve()
                with self.ledger.lock(tmppath):
                    # Inside lock: OS lock acquired while holding thread lock
                    self.assertIn("os_lock_acquired", events)
                    # The thread lock is held:
                    thread_lock = _PROJECT_LOCKS[proj_key][0]
                    # Acquire non-blocking from another thread must fail
                    other_thread_acquired = []

                    def try_acquire() -> None:
                        got = thread_lock.acquire(blocking=False)
                        other_thread_acquired.append(got)
                        if got:
                            thread_lock.release()

                    t = threading.Thread(target=try_acquire)
                    t.start()
                    t.join()
                    self.assertEqual(other_thread_acquired, [False])

                self.assertIn("os_lock_released", events)
                self.assertNotIn(proj_key, _PROJECT_LOCKS)

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
