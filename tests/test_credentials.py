"""API key resolution: the environment, then a private key file, then launchctl. The key never appears in output.

Ways this could fail, written before the code:
1. An empty TYPESAFE_API_KEY hides a valid key file.
2. A group- or world-readable key file is trusted, or one its owner cannot read reports a network error.
2b. The file is checked and read through different lookups, so it can be swapped for a symlink in between.
3. A symlinked key file is trusted (it could point anywhere).
4. A whitespace-only key file counts as a key.
5. A relative XDG_CONFIG_HOME is honoured (the XDG spec says to ignore it).
6. launchctl is missing, fails or hangs, and routing crashes instead of reporting no_api_key.
7. launchctl is consulted on a platform that does not have it.
8. The key text leaks into stdout, stderr or JSON when routing fails.
9. Error messages point at ~/.config when XDG_CONFIG_HOME sends the lookup elsewhere.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tink_route.cli import main
from tink_route.core import credentials
from tink_route.core.models import RoutingResult

SENTINEL = "sk-SENTINEL-do-not-print-0123456789"


class KeyResolutionTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        library = self.root / "tink-home" / "skills" / "probe"
        library.mkdir(parents=True)
        (library / "SKILL.md").write_text("---\nname: probe\ndescription: probe\n---\nprobe\n")
        self.config = self.root / "config"
        self.key_file = self.config / "tink-route" / "typesafe_api_key"
        env = {"TINK_HOME": str(self.root / "tink-home"), "XDG_CONFIG_HOME": str(self.config),
               "HOME": str(self.root / "home"), "TINK_ROUTE_RECEIPT": ""}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop("TYPESAFE_API_KEY", None)
        no_launchctl = patch.object(credentials, "_from_launchctl", return_value=None)
        no_launchctl.start()
        self.addCleanup(no_launchctl.stop)
        self.keys_seen: list[str] = []
        self.route_error: Exception | None = None
        test = self

        class FakeClient:
            def __init__(self, api_key, **_kwargs):
                test.keys_seen.append(api_key)

            def route(self, task, skills, **_kwargs):
                if test.route_error:
                    raise test.route_error
                return RoutingResult(status="routed", task=task, winner="probe", probability=1.0, confidence=1.0)

        client = patch("tink_route.flow.JevRouterClient", FakeClient)
        client.start()
        self.addCleanup(client.stop)

    def write_key(self, text: str, mode: int = 0o600) -> None:
        self.key_file.parent.mkdir(parents=True, exist_ok=True)
        self.key_file.write_text(text)
        self.key_file.chmod(mode)

    def pick(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--pick", "--json", "fixture task"])
        return code, json.loads(out.getvalue()), out.getvalue() + err.getvalue()

    def test_environment_key_is_used_first(self):
        os.environ["TYPESAFE_API_KEY"] = "env-key"
        self.write_key("file-key")
        code, result, _ = self.pick()
        self.assertEqual((code, result["status"], self.keys_seen), (0, "routed", ["env-key"]))

    def test_empty_environment_value_falls_through_to_the_key_file(self):
        os.environ["TYPESAFE_API_KEY"] = "  "
        self.write_key("file-key\n")
        code, _, _ = self.pick()
        self.assertEqual((code, self.keys_seen), (0, ["file-key"]))

    def test_private_key_file_is_used_when_the_environment_has_none(self):
        self.write_key("  file-key  \n")
        code, _, _ = self.pick()
        self.assertEqual((code, self.keys_seen), (0, ["file-key"]))
        self.assertEqual(credentials.resolve_api_key(), ("file-key", "file"))

    def test_readable_key_file_is_refused_before_any_call(self):
        for mode in (0o644, 0o640, 0o604):
            with self.subTest(mode=oct(mode)):
                self.write_key("file-key", mode)
                code, result, _ = self.pick()
                self.assertEqual((code, result["reason"]), (2, "key_file_insecure"))
        self.assertEqual(self.keys_seen, [])

    def test_key_file_its_owner_cannot_read_is_refused_not_a_network_error(self):
        if os.geteuid() == 0:
            self.skipTest("root can read any file")
        for mode in (0o200, 0o000):
            with self.subTest(mode=oct(mode)):
                self.write_key("file-key", mode)
                code, result, _ = self.pick()
                self.assertEqual((code, result["reason"]), (2, "key_file_insecure"))
                self.key_file.chmod(0o600)

    def test_messages_name_the_key_file_actually_consulted(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["fixture task"])
        self.assertIn(str(self.key_file), out.getvalue())
        self.write_key("file-key", 0o644)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["fixture task"])
        self.assertIn(f"key file {self.key_file} must", out.getvalue())

    def test_symlinked_key_file_is_refused(self):
        real = self.root / "elsewhere"
        real.write_text("file-key")
        real.chmod(0o600)
        self.key_file.parent.mkdir(parents=True)
        self.key_file.symlink_to(real)
        code, result, _ = self.pick()
        self.assertEqual((code, result["reason"], self.keys_seen), (2, "key_file_insecure", []))

    def test_whitespace_only_key_file_is_no_key(self):
        self.write_key(" \n\t\n")
        code, result, _ = self.pick()
        self.assertEqual((code, result["reason"], self.keys_seen), (2, "no_api_key", []))

    def test_relative_xdg_config_home_is_ignored(self):
        os.environ["XDG_CONFIG_HOME"] = "relative/config"
        self.assertEqual(credentials.key_file_path(),
                         Path(os.environ["HOME"]) / ".config" / "tink-route" / "typesafe_api_key")

    def test_launchctl_is_the_last_resort(self):
        with patch.object(credentials, "_from_launchctl", return_value="launchctl-key"):
            code, _, _ = self.pick()
            self.assertEqual(credentials.resolve_api_key(), ("launchctl-key", "launchctl"))
        self.assertEqual((code, self.keys_seen), (0, ["launchctl-key"]))

    def test_no_key_anywhere_reports_no_api_key(self):
        code, result, text = self.pick()
        self.assertEqual((code, result["reason"], self.keys_seen), (2, "no_api_key", []))
        self.assertEqual(credentials.resolve_api_key(), (None, None))

    def test_key_never_appears_in_output_when_routing_fails(self):
        self.write_key(SENTINEL)
        self.route_error = RuntimeError(f"401 for key {SENTINEL}")
        for argv in (["--pick", "--json"], ["--pick"], ["--json"], []):
            with self.subTest(argv=argv):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = main([*argv, "fixture task"])
                self.assertEqual(code, 2)
                self.assertNotIn(SENTINEL, out.getvalue() + err.getvalue())


class LaunchctlTest(unittest.TestCase):
    """The launchctl lookup itself, with the platform and the process call faked."""

    def run_launchctl(self, platform: str, result=None, error: Exception | None = None):
        def fake_run(cmd, **kwargs):
            self.calls.append((cmd, kwargs.get("timeout")))
            if error:
                raise error
            return result

        self.calls: list = []
        with patch.object(credentials.sys, "platform", platform), \
                patch.object(credentials.subprocess, "run", fake_run):
            return credentials._from_launchctl()

    def test_reads_the_value_on_macos_with_a_short_timeout(self):
        done = subprocess.CompletedProcess([], 0, stdout="launchctl-key\n", stderr="")
        self.assertEqual(self.run_launchctl("darwin", done), "launchctl-key")
        self.assertEqual(self.calls, [(["launchctl", "getenv", "TYPESAFE_API_KEY"], 2)])

    def test_failures_mean_not_found(self):
        cases = [(None, FileNotFoundError("launchctl")),
                 (None, subprocess.TimeoutExpired("launchctl", 2)),
                 (subprocess.CompletedProcess([], 1, stdout="", stderr="boom"), None),
                 (subprocess.CompletedProcess([], 0, stdout="  \n", stderr=""), None)]
        for result, error in cases:
            with self.subTest(error=error, result=result):
                self.assertIsNone(self.run_launchctl("darwin", result, error))

    def test_not_consulted_elsewhere(self):
        self.assertIsNone(self.run_launchctl("linux", error=AssertionError("must not run")))
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
