"""Clarity/stability batch: usage errors, --help layout, legacy deprecation note, versioning."""
import contextlib
import io
import os
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import tink_route
from tink_route import cli
from tink_route.adapters import client as client_mod

REPO = Path(__file__).resolve().parent.parent
TRY = 'Try: tink-route --help  (agent-initiated skills: tink-route --use "<task>")'


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = cli.main(argv)
        except SystemExit as e:
            code = e.code
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
        self.check(["--bogus"], "--bogus")

    def test_bad_stage_choice(self):
        self.check(["--use", "--stage", "nope", "t"], "nope")

    def test_bad_type(self):
        self.check(["--top-k", "abc", "t"], "abc")

    def test_missing_value(self):
        self.check(["--use", "--stage"], "--stage")


class TestHelp(unittest.TestCase):
    def help(self):
        code, out, _ = run(["--help"])
        self.assertEqual(code, 0)
        return out

    def test_use_group_first(self):
        h = self.help()
        a = h.index("Agent-initiated skills (recommended)")
        b = h.index("Legacy: persistent install (deprecated; prefer --use)")
        self.assertLess(a, b)
        recommended, legacy = h[a:b], h[b:]
        for flag in ("--use", "--stage", "--skillset", "--stage-only", "--receipt",
                     "--inline-max", "--json", "--deadline", "--library", "--model"):
            self.assertIn(flag, recommended, flag)
        for flag in ("--install", "--prune", "--all-unpinned", "--dry-run", "--ephemeral",
                     "--multi", "--top-k", "--check"):
            self.assertIn(flag, legacy, flag)
            self.assertNotIn(f"  {flag}", recommended.split("Legacy")[0])

    def test_legacy_flags_marked_deprecated(self):
        h = self.help()
        legacy = h[h.index("Legacy: persistent install"):]
        for flag in ("--install", "--prune", "--all-unpinned", "--dry-run", "--ephemeral",
                     "--multi", "--top-k", "--check"):
            block = re.search(rf"^\s+[^\n]*{re.escape(flag)}[^\n]*\n(?:\s{{10,}}[^\n]*\n)*", legacy, re.M)
            self.assertIsNotNone(block, flag)
            self.assertIn("[deprecated]", block.group(0), flag)

    def test_epilog_kept(self):
        h = self.help()
        self.assertIn("tink-route --use --stage <stage>", h)
        self.assertIn("Exit codes: 0 skill delivered", h)

    def test_flags_still_parse(self):
        p = cli.build_parser()
        a = p.parse_args(["-i", "--no-ephemeral", "--multi", "--top-k", "3", "--dry-run", "t"])
        self.assertTrue(a.install and a.multi and a.dry_run)
        self.assertFalse(a.ephemeral)
        self.assertEqual(a.top_k, 3)


class TestLegacyNote(unittest.TestCase):
    NOTE = "note: persistent install (-i/--prune) is deprecated; prefer `tink-route --use`."

    def setUp(self):
        cli._LEGACY_NOTED = False
        self.tmp = tempfile.mkdtemp()
        self.prev = os.getcwd()
        os.chdir(self.tmp)
        self.addCleanup(os.chdir, self.prev)

    def test_prune_notes_once_per_process(self):
        _, _, err = run(["--prune"])
        self.assertEqual(err.splitlines().count(self.NOTE), 1)
        _, _, err2 = run(["--prune"])
        self.assertNotIn(self.NOTE, err2)

    def test_prune_json_stdout_clean(self):
        _, out, err = run(["--prune", "--json"])
        self.assertIn(self.NOTE, err)
        self.assertNotIn("deprecated", out)

    def test_install_notes(self):
        with patch.dict(os.environ):
            os.environ.pop("TYPESAFE_API_KEY", None)
            _, _, err = run(["-i", "task"])
        self.assertIn(self.NOTE, err)

    def test_plain_recommend_and_use_do_not_note(self):
        with patch.dict(os.environ):
            os.environ.pop("TYPESAFE_API_KEY", None)
            _, _, err = run(["task"])
            self.assertNotIn(self.NOTE, err)
            _, _, err = run(["--use", "task"])
            self.assertNotIn(self.NOTE, err)


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
        self.assertEqual(tink_route.__version__, "0.7.0")


if __name__ == "__main__":
    unittest.main()
