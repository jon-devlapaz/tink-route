"""Regression tests for dogfood findings (subagents sa-1/sa-2/sa-3), Jev-prioritized."""

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from tink_route.adapters.client import JevRouterClient
from tink_route.adapters.executor import SubprocessExecutor
from tink_route.core.engine import RoutingEngine
from tink_route.core.models import RoutingResult
from tink_route.metadata import load_library_skills, parse_skill_metadata


class FakeExecutor(SubprocessExecutor):
    def __init__(self) -> None:
        self.calls: list = []

    def run(self, cmd: list, cwd: Path) -> tuple:
        self.calls.append((cmd, cwd))
        return 0, "ok", ""


class TestLibraryRobustness(unittest.TestCase):
    def test_malformed_skill_skipped_good_skill_loads(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td)
            (lib / "good").mkdir()
            (lib / "good" / "SKILL.md").write_text("---\nname: good\ndescription: fine\n---\nbody")
            (lib / "badname").mkdir()
            (lib / "badname" / "SKILL.md").write_text("---\nname: bad name\ndescription: x\n---\n")
            skills = load_library_skills(lib)
        self.assertEqual([s["name"] for s in skills], ["good"])

    def test_unclosed_frontmatter_treated_as_body(self) -> None:
        meta = parse_skill_metadata("---\nname: foo\ndescription: bar\nno closing", "fallback")
        self.assertEqual(meta["description"], "")
        self.assertIn("name: foo", meta.get("body", ""))

    def test_non_utf8_bytes_do_not_crash_loader(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            lib = Path(td)
            (lib / "weird").mkdir()
            (lib / "weird" / "SKILL.md").write_bytes(b"---\nname: weird\ndescription: hi \xff\xfe bad\n---\n")
            skills = load_library_skills(lib)
        self.assertEqual(len(skills), 1)


class TestEngineContract(unittest.TestCase):
    def test_failed_install_clears_references(self) -> None:
        class FailExec(FakeExecutor):
            def run(self, cmd: list, cwd: Path) -> tuple:
                self.calls.append((cmd, cwd))
                return 1, "", "boom"

        client = MagicMock()
        client.route.return_value = RoutingResult(status="routed", task="t", winner="s", probability=0.9)
        ledger = MagicMock()
        ledger.lock.return_value.__enter__.return_value = None
        eng = RoutingEngine(client=client, executor=FailExec(), ledger=ledger)
        with tempfile.TemporaryDirectory() as td:
            res = eng.route("t", [{"name": "s", "description": "d"}], install=True, project_dir=Path(td))
        self.assertEqual(res.references, [])
        self.assertEqual(res.scripts, [])
        self.assertIsNotNone(res.install_error)

    def test_reserved_name_rejected_on_install(self) -> None:
        exe = FakeExecutor()
        eng = RoutingEngine(client=MagicMock(), executor=exe, ledger=MagicMock())
        with tempfile.TemporaryDirectory() as td:
            out = eng.install_skill_locked("manage-tink", Path(td))
        self.assertFalse(out.success)
        self.assertEqual(out.code, 2)
        self.assertEqual(exe.calls, [])


class TestMultiPool(unittest.TestCase):
    def test_multi_draws_from_all_batches(self) -> None:
        client = JevRouterClient(api_key="k")
        skills = [{"name": f"s{i}", "description": f"d{i}"} for i in range(30)]
        gate = {"answers": {"specialised_workflow": {"noul": 0.9}}}
        b1 = {"answers": {"selected_skill": {
            "choice": "s0", "confidence": 0.99,
            "probabilities": {"s0": 0.99, **{f"s{i}": 0.95 for i in range(1, 24)}}}}}
        b2 = {"answers": {"selected_skill": {
            "choice": "s24", "confidence": 0.61,
            "probabilities": {"s24": 0.61, **{f"s{i}": 0.1 for i in range(25, 30)}}}}}
        final = {"answers": {"selected_skill": {
            "choice": "s0", "confidence": 0.99,
            "probabilities": {"s0": 0.99, "s24": 0.61}}}}
        with patch.object(client, "_call_api", side_effect=[gate, b1, b2, final]):
            res = client.route("big task", skills, multi=True, top_k=3,
                               tri_gate=False, rerank=False)
        names = [c["skill"] for c in res.candidates or []]
        self.assertIn("s1", names)


class TestCliFixes(unittest.TestCase):
    def _run_main(self, argv: list, cwd: Path) -> tuple:
        from tink_route.cli import main
        buf_out, buf_err = io.StringIO(), io.StringIO()
        with patch("pathlib.Path.cwd", return_value=cwd), \
             patch.object(sys, "argv", argv), \
             redirect_stdout(buf_out), redirect_stderr(buf_err):
            code = main()
        return code, buf_out.getvalue(), buf_err.getvalue()

    def test_all_unpinned_without_ledger_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            code, _, err = self._run_main(["tink-route", "prune", "--all-unpinned"], Path(td))
        self.assertEqual(code, 0)
        self.assertIn("Warning", err)

    def test_all_unpinned_with_corrupt_ledger_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmppath = Path(td)
            tink = tmppath / ".tink"
            tink.mkdir()
            (tink / "ephemeral.json").write_text("{corrupt")
            code, _, err = self._run_main(["tink-route", "prune", "--all-unpinned", "--dry-run"], tmppath)
        self.assertEqual(code, 0)
        self.assertIn("Warning", err)

    def test_all_unpinned_with_empty_ledger_warns(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            tmppath = Path(td)
            tink = tmppath / ".tink"
            tink.mkdir()
            (tink / "ephemeral.json").write_text('{"version": 1, "skills": []}')
            code, _, err = self._run_main(["tink-route", "prune", "--all-unpinned", "--dry-run"], tmppath)
        self.assertEqual(code, 0)
        self.assertIn("Warning", err)

    def test_prune_empty_non_dry_exits_zero(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            code, _, _ = self._run_main(["tink-route", "prune"], Path(td))
        self.assertEqual(code, 0)

    def test_missing_library_dir_has_own_error(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            with patch.dict("os.environ", {"TYPESAFE_API_KEY": "k"}):
                code, _, err = self._run_main(
                    ["tink-route", "--library", str(Path(td) / "nope"), "do things"], Path(td))
        self.assertEqual(code, 2)
        self.assertIn("not found or not a directory", err)

if __name__ == "__main__":
    unittest.main()
