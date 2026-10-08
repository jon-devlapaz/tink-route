"""tink-inject eval, doctor and status: measure what is actually delivered, and make a broken setup obvious.

Same harness as test_inject_cli: the real command, library, approvals, threshold and mount parsing; a faked routing
service and a fake `tink` binary.

Ways this could fail, written before the code:
1. The eval scores the router's whole-library pick, which delivery may never hand out (an unapproved skill), so
   its recall overstates real delivery.
2. The eval prints a score on a broken setup (no key, router down): a meaningless number that looks real.
3. The eval writes into the repository it runs in.
4. doctor passes when there is no key, tink is missing, approvals are unreadable, or the canary can't deliver;
   or it prints the key.
5. doctor treats INJECT=off as healthy in strict mode (an experiment arm that injects nothing).
6. status can't tell a run with degraded rows from a healthy one, or ignores --since.
7. eval/doctor/status/lint hit an exception (missing or malformed cases file) and exit 0 with no output, so
   automation reads a run that never happened as a pass.
"""
import json
from pathlib import Path
import subprocess
import sys
import unittest

from test_inject_cli import FAKE_TINK, SENTINEL, WRAPPER, InjectCliTest


class InjectMeasureTest(InjectCliTest):
    """Reuses the hook harness (fixtures and helpers); none of its test methods run here."""

    def setUp(self):
        super().setUp()
        unapproved = self.tink_home / "skills" / "a-unapproved"
        unapproved.mkdir()
        (unapproved / "SKILL.md").write_text("---\nname: a-unapproved\ndescription: Apply when debugging.\n---\nX\n")
        self.cases = self.root / "cases.json"
        self.cases.write_text(json.dumps([
            {"need": "find the root cause of a bug", "expect": "root-cause", "also": ["a-unapproved"]},
            {"need": "nothing fits this", "expect": None},
        ]))

    def run_cmd(self, *argv, **env):
        return subprocess.run([sys.executable, str(self.wrapper), *argv], capture_output=True, text=True,
                              cwd=self.repo, env={**self.env, **env}, timeout=60)

    # eval ------------------------------------------------------------------------------------------------------

    def test_eval_reports_routed_and_delivered_separately(self):
        result = self.run_cmd("eval", str(self.cases))
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout
        # The whole library routes to the unapproved skill; delivery hands out the approved one.
        self.assertRegex(out, r"routed a-unapproved +delivered root-cause")
        self.assertIn("delivered recall 1/1", out)
        self.assertIn("routed recall 1/1", out)
        self.assertIn("correct abstention 1/1", out)
        self.assertIn("routed but not deliverable: a-unapproved", out)

    def test_eval_refuses_to_score_a_broken_setup(self):
        result = self.run_cmd("eval", str(self.cases), FAKE_ROUTER="nokey")
        self.assertEqual(result.returncode, 2)
        self.assertNotIn("recall", result.stdout)
        self.assertIn("no_api_key", result.stdout + result.stderr)

    def test_eval_writes_nothing_into_the_repository(self):
        self.run_cmd("eval", str(self.cases))
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.repo,
                                capture_output=True, text=True, env=self.env).stdout
        self.assertEqual(status, "")

    def test_diagnostic_commands_fail_loudly_on_exceptions(self):
        bad = self.root / "bad.json"
        bad.write_text("{not json")
        for argv in (["eval", str(self.root / "missing.json")], ["eval", str(bad)]):
            with self.subTest(argv=argv):
                result = self.run_cmd(*argv)
                self.assertEqual(result.returncode, 2)
                self.assertIn("tink-inject:", result.stderr)

    # doctor ----------------------------------------------------------------------------------------------------

    def test_doctor_passes_a_working_setup_and_never_prints_the_key(self):
        result = self.run_cmd("doctor", TYPESAFE_API_KEY=SENTINEL)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("PASS key: from env", result.stdout)
        self.assertIn("PASS canary delivers: root-cause", result.stdout)
        self.assertNotIn(SENTINEL, result.stdout + result.stderr)
        data = json.loads(self.run_cmd("doctor", "--json", TYPESAFE_API_KEY=SENTINEL).stdout)
        self.assertTrue(data["ok"])
        self.assertNotIn(SENTINEL, json.dumps(data))

    def test_doctor_names_each_broken_part(self):
        cases = {
            "key": ({"XDG_CONFIG_HOME": str(self.root / "no-config")}, None),
            "tink": ({"TYPESAFE_API_KEY": "k", "PATH": self.env["PATH"].split(":", 1)[1]}, None),
            "approvals": ({"TYPESAFE_API_KEY": "k"}, "approvals"),
            "canary routes": ({"TYPESAFE_API_KEY": "k", "FAKE_ROUTER": "crash"}, None),
        }
        for check, (env, breakage) in cases.items():
            with self.subTest(check=check):
                if breakage == "approvals":
                    (self.tink_home / "approvals.json").write_text("{oops")
                result = self.run_cmd("doctor", **env)
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertRegex(result.stdout, rf"FAIL {check}")
                (self.tink_home / "approvals.json").write_text(
                    json.dumps({"version": 1, "skills": {"root-cause": "sha256:x"}}))

    def test_doctor_strict_fails_when_injection_is_off(self):
        relaxed = self.run_cmd("doctor", TYPESAFE_API_KEY="k", INJECT="off")
        self.assertEqual(relaxed.returncode, 0, relaxed.stdout)
        self.assertIn("WARN injection enabled", relaxed.stdout)
        strict = self.run_cmd("doctor", "--strict", TYPESAFE_API_KEY="k", INJECT="off")
        self.assertEqual(strict.returncode, 1)
        self.assertIn("FAIL injection enabled", strict.stdout)

    # status ----------------------------------------------------------------------------------------------------

    def test_status_counts_outcomes_and_flags_degradation(self):
        self.hook()                                           # deliver
        self.hook("needs: nothing fits this", session="s2")   # abstain
        healthy = self.run_cmd("status")
        self.assertEqual(healthy.returncode, 0, healthy.stdout)
        self.assertIn("delivered 1", healthy.stdout)
        self.assertIn("abstained 1", healthy.stdout)
        self.assertIn("degraded 0", healthy.stdout)
        self.hook(session="s3", FAKE_ROUTER="nokey")          # degraded
        broken = self.run_cmd("status")
        self.assertEqual(broken.returncode, 1)
        self.assertIn("degraded 1 (no_api_key)", broken.stdout)
        later = self.run_cmd("status", "--since", "2999-01-01T00:00:00")
        self.assertEqual(later.returncode, 0)
        self.assertIn("delivered 0", later.stdout)


# The base class's tests are already run by test_inject_cli; don't run them twice.
for name in [n for n in dir(InjectCliTest) if n.startswith("test_")]:
    setattr(InjectMeasureTest, name, None)
del InjectCliTest

if __name__ == "__main__":
    unittest.main()
