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
8. An approved skill that changed since approval wins routing, is refused at delivery, and hides an approved
   runner-up (the very failure the flag exists to remove), or the refusal goes unreported.
9. Tink's approvals, which cover only Tink's library, are applied to a custom --library.
10. Two unapproved skills sharing a name make --approved-only fail, though neither could be delivered.
11. When the re-route after a refusal fails, the JSON keeps the discarded winner's confidence and probability.
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
    """`tink mount`: a payload for --json --payload, nothing otherwise. Names in `refuse` fail with that code."""
    refuse: dict = {}

    def run(self, cmd, cwd=None):
        name = cmd[2]
        if name in self.refuse:
            return 1, json.dumps({"code": self.refuse[name]}), ""
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
        FakeExecutor.refuse = {}
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
        self.stderr = err.getvalue()
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

    def test_changed_approved_skill_is_skipped_and_the_runner_up_delivered(self):
        for code_name in ("digest_mismatch", "unapproved"):
            with self.subTest(refusal=code_name):
                FakeExecutor.refuse = {"approved-one": code_name}
                self.offered.clear()
                code, result = self.run_cli("--approved-only")
                self.assertEqual((code, result["status"], result["skill"]), (0, "delivered", "approved-two"))
                self.assertEqual(result["skipped"], [{"skill": "approved-one", "reason": code_name}])
                self.assertEqual(self.offered, [["approved-one", "approved-two"], ["approved-two"]])
                self.assertIn("skipped approved-one", self.stderr)

    def test_only_one_reroute_is_attempted(self):
        FakeExecutor.refuse = {"approved-one": "digest_mismatch", "approved-two": "digest_mismatch"}
        code, result = self.run_cli("--approved-only")
        self.assertEqual((code, result["status"], result["reason"]), (2, "error", "digest_mismatch"))
        self.assertEqual(len(self.offered), 2)

    def test_default_mode_reports_the_refusal_without_rerouting(self):
        FakeExecutor.refuse = {"approved-one": "digest_mismatch"}
        code, result = self.run_cli()
        self.assertEqual((code, result["reason"], result["skipped"]), (2, "digest_mismatch", []))
        self.assertEqual(len(self.offered), 1)

    def test_approvals_do_not_apply_to_a_custom_library(self):
        custom = self.home.parent / "custom"
        (custom / "approved-one").mkdir(parents=True)
        (custom / "approved-one" / "SKILL.md").write_text("---\nname: approved-one\ndescription: x\n---\n")
        code, result = self.run_cli("--pick", "--approved-only", "--library", str(custom))
        self.assertEqual((code, result["reason"]), (2, "approvals_library_mismatch"))
        self.assertEqual(self.offered, [])

    def test_duplicate_names_among_unapproved_skills_do_not_block_approved_routing(self):
        for folder in ("dup-a", "dup-b"):
            (self.home / "skills" / folder).mkdir()
            (self.home / "skills" / folder / "SKILL.md").write_text("---\nname: shared\ndescription: d\n---\n")
        code, result = self.run_cli("--pick", "--approved-only")
        self.assertEqual((code, result["status"]), (0, "routed"))
        self.assertEqual(self.offered[-1], ["approved-one", "approved-two"])
        code, result = self.run_cli("--pick")  # the whole library still refuses an ambiguous name
        self.assertEqual((code, result["reason"]), (2, "library_unreadable"))

    def test_failed_reroute_does_not_report_the_discarded_winners_numbers(self):
        FakeExecutor.refuse = {"approved-one": "digest_mismatch"}
        calls = []

        def route(task, skills, args):
            calls.append(1)
            if len(calls) == 2:
                raise RuntimeError("network")
            return RoutingResult(status="routed", task=task, winner="approved-one", probability=0.9, confidence=0.7)

        self.route = route
        code, result = self.run_cli("--approved-only")
        self.assertEqual((code, result["reason"]), (2, "route_failed"))
        self.assertEqual((result["skill"], result["confidence"], result["probability"]), (None, None, None))
        self.assertEqual(result["skipped"], [{"skill": "approved-one", "reason": "digest_mismatch"}])

    def test_delivery_json_reports_probability_beside_confidence(self):
        code, result = self.run_cli("--approved-only")
        self.assertEqual((result["probability"], result["confidence"]), (0.9, 0.7))


if __name__ == "__main__":
    unittest.main()
