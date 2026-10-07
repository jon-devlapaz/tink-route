"""--approved-only: route only among skills Tink has approved, so the decision matches what delivery can hand out.

Without it, the router can pick an unapproved skill that delivery then refuses (`unapproved`), and that unapproved
winner hides an approved runner-up. An eval that uses --pick also overstates what delivery will do.

Ways this could fail, written before the code:
1. An unapproved skill is still offered to the router.
2. A missing approvals file silently falls back to the whole library.
3. A malformed file (bad JSON, wrong version, wrong shape) is treated as "all" or "none" without saying so.
4. Empty approvals look like "no skill applies" instead of naming the problem.
5. The default behaviour (no flag) changes.
6. --pick and delivery offer different candidates under the flag.
7. JSON gives callers no way to tell the flag was in force, or which number the threshold gated on.
"""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tink_route.cli import main
from tink_route.core.models import RoutingResult


class FakeExecutor:
    """`tink mount`: a payload for --json --payload, nothing otherwise."""

    def run(self, cmd, cwd=None):
        name = cmd[2]
        if "--payload" in cmd:
            return 0, json.dumps({"skill": name, "tree_digest": "sha256:" + "0" * 64,
                                  "payload": {"content": f"BODY {name}", "chars": 9}}), ""
        return 0, "", ""


class ApprovedOnlyTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name) / "tink-home"
        for name in ("approved-one", "approved-two", "unapproved"):
            (self.home / "skills" / name).mkdir(parents=True)
            (self.home / "skills" / name / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n")
        self.approve("approved-one", "approved-two", "not-in-library")
        env = patch.dict(os.environ, {"TINK_HOME": str(self.home), "TINK_ROUTE_RECEIPT": ""})
        env.start()
        self.addCleanup(env.stop)
        executor = patch("tink_route.cli.DefaultSubprocessExecutor", FakeExecutor)
        executor.start()
        self.addCleanup(executor.stop)
        self.offered: list[list[str]] = []

    def approve(self, *names):
        (self.home / "approvals.json").write_text(json.dumps(
            {"version": 1, "skills": {n: "sha256:" + "1" * 64 for n in names}}))

    def route(self, task, skills, args):
        self.offered.append(sorted(s["name"] for s in skills))
        return RoutingResult(status="routed", task=task, winner=skills[0]["name"], probability=0.9, confidence=0.7)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(["--json", *argv, "fixture task"], route_fn=self.route)
        return code, json.loads(out.getvalue())

    def test_only_approved_skills_reach_the_router_in_pick_and_delivery(self):
        for argv in (["--pick", "--approved-only"], ["--approved-only"]):
            with self.subTest(argv=argv):
                code, result = self.run_cli(*argv)
                self.assertEqual(code, 0)
                self.assertTrue(result["approved_only"])
                self.assertEqual(self.offered[-1], ["approved-one", "approved-two"])
        self.assertEqual(result["status"], "delivered")
        self.assertEqual(result["content"], "BODY approved-one")

    def test_default_still_routes_over_the_whole_library(self):
        for argv in (["--pick"], []):
            with self.subTest(argv=argv):
                code, result = self.run_cli(*argv)
                self.assertFalse(result["approved_only"])
                self.assertEqual(self.offered[-1], ["approved-one", "approved-two", "unapproved"])

    def test_unreadable_approvals_never_fall_back_to_the_whole_library(self):
        cases = {"missing": None, "not json": "{oops", "wrong version": {"version": 2, "skills": {}},
                 "skills not a map": {"version": 1, "skills": ["approved-one"]}, "not an object": [1]}
        for label, content in cases.items():
            with self.subTest(case=label):
                path = self.home / "approvals.json"
                path.unlink(missing_ok=True)
                if content is not None:
                    path.write_text(content if isinstance(content, str) else json.dumps(content))
                for argv in (["--pick", "--approved-only"], ["--approved-only"]):
                    code, result = self.run_cli(*argv)
                    self.assertEqual((code, result["status"], result["reason"]), (2, "error", "approvals_unreadable"))
        self.assertEqual(self.offered, [])

    def test_no_approved_skills_is_named_not_mistaken_for_no_match(self):
        self.approve()
        code, result = self.run_cli("--approved-only")
        self.assertEqual((code, result["reason"]), (2, "no_approved_skills"))
        self.assertEqual(self.offered, [])

    def test_failure_sentences_name_the_fix(self):
        self.approve()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            main(["--approved-only", "fixture task"], route_fn=self.route)
        self.assertIn("tink library approve", out.getvalue())
        (self.home / "approvals.json").write_text("{oops")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            main(["--pick", "--approved-only", "fixture task"], route_fn=self.route)
        self.assertIn("approvals", err.getvalue())

    def test_delivery_json_reports_probability_beside_confidence(self):
        code, result = self.run_cli("--approved-only")
        self.assertEqual((result["probability"], result["confidence"]), (0.9, 0.7))


if __name__ == "__main__":
    unittest.main()
