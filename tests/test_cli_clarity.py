"""CLI surface: one obvious command line, short help, removed flags rejected, versioning."""
import contextlib
import io
import re
import unittest
from pathlib import Path
from unittest.mock import patch

import tink_route
from tink_route import cli
from tink_route.adapters import client as client_mod

REPO = Path(__file__).resolve().parent.parent
TRY = "Try: tink-route --help"
REMOVED = ["--use", "--stage", "--stage-only", "-i", "--install", "--prune", "--all-unpinned",
           "--dry-run", "--ephemeral", "--no-ephemeral", "--multi", "--no-multi", "--top-k", "--check",
           "--strict"]
ADVANCED = ["--library", "--model", "--threshold", "--tri-gate", "--no-tri-gate", "--rerank",
            "--no-rerank", "--fits-threshold", "--deadline", "--inline-max"]
MAIN = ["--skillset", "--anywhere", "--receipt", "--json", "--pick", "--version"]


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = cli.main(argv)
    return code, out.getvalue(), err.getvalue()


class TestUsageErrors(unittest.TestCase):
    def check(self, argv, needle):
        code, out, err = run(argv)
        self.assertEqual(code, 2, argv)
        self.assertEqual(out, "")
        lines = err.splitlines()
        self.assertEqual(len(lines), 2, err)
        self.assertTrue(lines[0].startswith("tink-route: error: "), lines[0])
        self.assertIn(needle, lines[0])
        self.assertEqual(lines[1], TRY)
        self.assertNotIn("usage:", err)

    def test_unknown_flag(self):
        self.check(["--bogus", "t"], "--bogus")

    def test_bad_type(self):
        self.check(["--inline-max", "abc", "t"], "abc")

    def test_missing_value(self):
        self.check(["--skillset"], "--skillset")

    def test_abbreviations_are_not_accepted(self):
        self.check(["--inl", "5", "t"], "--inl")

    def test_removed_flags_are_rejected(self):
        for flag in REMOVED:
            with self.subTest(flag=flag):
                self.check([flag, "t"], flag)

    def test_removed_flags_with_values_are_rejected(self):
        self.check(["--stage", "build", "t"], "--stage")
        self.check(["--top-k", "3", "t"], "--top-k")


class TestHelp(unittest.TestCase):
    def help(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, 0)
        return out

    def test_no_removed_flags_or_deprecation(self):
        h = self.help()
        for flag in REMOVED:
            self.assertIsNone(re.search(rf"(?<![\w-]){re.escape(flag)}(?![\w-])", h), flag)
        for word in ("[deprecated]", "deprecated", "Legacy", "install", "prune", "ephemeral"):
            self.assertNotIn(word, h, word)

    def test_help_is_short(self):
        self.assertLessEqual(len(self.help().splitlines()), 45)

    def test_tuning_flags_live_under_advanced(self):
        h = self.help()
        main_part, advanced = h.split("Advanced", 1)
        for flag in ADVANCED:
            self.assertIn(flag, advanced, flag)
            self.assertNotIn(flag, main_part, flag)
        for flag in MAIN:
            self.assertIn(flag, main_part, flag)
        self.assertIn("-h, --help", main_part)

    def test_epilog_has_agent_line_and_exit_codes(self):
        h = self.help()
        self.assertIn('tink-route "<what you need>" and follow the output', h)
        self.assertNotIn("<stage>", h)
        self.assertNotIn("tink:rules", h)
        self.assertIn("whole skill library", h)
        self.assertIn("--anywhere", h)
        self.assertIn("if it exits non-zero, continue without it", h)
        self.assertIn("Exit codes: 0 delivered", h)

    def test_tuning_flags_still_parse(self):
        a = cli.build_parser().parse_args(
            ["--no-rerank", "--no-tri-gate", "--threshold", "0.7", "--fits-threshold", "0.4",
             "--deadline", "5", "--inline-max", "9", "--library", "/x", "--model", "m", "t"])
        self.assertFalse(a.rerank or a.tri_gate)
        self.assertEqual((a.threshold, a.fits_threshold, a.deadline, a.inline_max), (0.7, 0.4, 5.0, 9))

    def test_version_flag(self):
        code, out, _ = run(["--version"])
        self.assertEqual((code, out.strip()), (0, f"tink-route {tink_route.__version__}"))


class TestVersioning(unittest.TestCase):
    def test_user_agent_derives_from_version(self):
        captured = {}

        class Resp:
            def __enter__(s): return s
            def __exit__(s, *a): return False
            def read(s): return b'{"choices": []}'

        def fake_urlopen(req, timeout=None):
            captured["ua"] = req.get_header("User-agent")
            return Resp()

        c = client_mod.JevRouterClient(api_key="k", max_retries=0)
        with patch.object(client_mod.urllib.request, "urlopen", fake_urlopen):
            try:
                c._call_api({"x": 1})
            except Exception:
                pass
        self.assertEqual(captured["ua"], f"tink-route/{tink_route.__version__}")

    def test_versions_agree(self):
        m = re.search(r'^version\s*=\s*"([^"]+)"', (REPO / "pyproject.toml").read_text(), re.M)
        self.assertEqual(m.group(1), tink_route.__version__)
        self.assertEqual(tink_route.__version__, "0.11.0")


if __name__ == "__main__":
    unittest.main()
