"""In-process E2E for `tink-route` (delivery and `--pick`).

The routing decision is faked through the injectable `route_fn` seam; everything
else (mounting, approval, digest verification) is the REAL sibling `tink` binary
reached through a PATH shim in an isolated TINK_HOME.
"""
import contextlib
import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tink_route import cli
from tink_route.core.models import RoutingResult

REPO = Path(__file__).resolve().parent.parent


def find_tink() -> str | None:
    cands = [os.environ.get("TINK_BIN"), str(REPO.parent / "tink" / "target" / "debug" / "tink")]
    for c in cands:
        if c and os.access(c, os.X_OK):
            return c
    return None


TINK = find_tink()


def skill_md(name: str, desc: str, body: str) -> str:
    return f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n"


@unittest.skipIf(TINK is None, "sibling tink binary not built")
class UseFlowCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp()).resolve()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.home = self.tmp / "home"
        (self.home / "skills").mkdir(parents=True)
        (self.home / "layout.json").write_text('{\n  "kind": "tink-skill-inventory"\n}\n')
        self.shim = self.tmp / "shim"
        self.shim.mkdir()
        os.symlink(TINK, self.shim / "tink")
        self.proj = self.tmp / "proj"
        self.proj.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.proj, check=True)
        env = {"TINK_HOME": str(self.home), "PATH": f"{self.shim}{os.pathsep}/usr/bin:/bin"}
        p = patch.dict(os.environ, env)
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("TINK_ROUTE_RECEIPT", None)
        self.prev = os.getcwd()
        os.chdir(self.proj)
        self.addCleanup(os.chdir, self.prev)
        self.seen: list[list[str]] = []
        self.tasks: list[str] = []

    # helpers
    def add_skill(self, name: str, body: str = "Do the thing.", desc: str | None = None) -> Path:
        d = self.home / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(skill_md(name, desc or f"{name} specialist procedure", body))
        return d

    def approve(self) -> None:
        subprocess.run([TINK, "library", "approve", "--all"], cwd=self.proj, capture_output=True)

    def pin(self, name: str, members: list[str], required=None) -> None:
        sd = self.home / "skillsets"
        sd.mkdir(exist_ok=True)
        data = {"source": "x", "revision": "r", "sourceRoot": "s", "members": members}
        if required is not None:
            data["required"] = required
        (sd / f"{name}.json").write_text(json.dumps(data))

    def router(self, status="routed", winner="alpha", confidence=0.91):
        def fn(task, skills, args):
            self.seen.append([s["name"] for s in skills])
            self.tasks.append(task)
            return RoutingResult(status=status, task=task,
                                 winner=winner if status == "routed" else None,
                                 confidence=confidence, probability=confidence)
        return fn

    def run_cli(self, argv, route_fn=None):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv, route_fn=route_fn or self.router())
        return code, out.getvalue(), err.getvalue()

    def receipt_lines(self, path: Path) -> list[dict]:
        return [json.loads(l) for l in path.read_text().splitlines()]

    def real_digest(self, name: str) -> str:
        p = subprocess.run([TINK, "mount", name, "--json"], cwd=self.proj, capture_output=True, text=True)
        return json.loads(p.stdout)["tree_digest"]


class TestDelivery(UseFlowCase):
    def test_inline_exact_header_and_payload(self) -> None:
        self.add_skill("alpha", "Step 1. Step 2.")
        self.approve()
        code, out, _ = self.run_cli(["do alpha stuff"])
        self.assertEqual(code, 0)
        content = (self.home / "skills/alpha/SKILL.md").read_text()
        digest = self.real_digest("alpha")
        self.assertTrue(digest.startswith("sha256:"))
        expected = (
            f"# tink skill: alpha  (digest {digest}, {len(content)} chars, confidence 0.91)\n"
            "Apply these instructions to the current task.\n\n" + content
        )
        self.assertEqual(out, expected)

    def test_large_skill_delivered_by_path_without_content(self) -> None:
        self.add_skill("alpha", "SECRET-BODY " * 50)
        self.approve()
        code, out, _ = self.run_cli(["--inline-max", "100", "do alpha stuff"])
        self.assertEqual(code, 0)
        self.assertNotIn("SECRET-BODY", out)
        self.assertIn("# tink skill: alpha  (digest sha256:", out)
        self.assertIn("read it in full before continuing: .tink/.active/alpha/SKILL.md", out)
        self.assertNotIn("Apply these instructions", out)
        link = self.proj / ".tink/.active/alpha"
        self.assertTrue(link.is_symlink())
        self.assertIn("SECRET-BODY", (link / "SKILL.md").read_text())

    def test_boundary_inline_max_is_inclusive(self) -> None:
        self.add_skill("alpha", "x")
        self.approve()
        n = len((self.home / "skills/alpha/SKILL.md").read_text())
        _, out, _ = self.run_cli(["--inline-max", str(n), "t"])
        self.assertIn("Apply these instructions", out)
        _, out, _ = self.run_cli(["--inline-max", str(n - 1), "t"])
        self.assertNotIn("Apply these instructions", out)

    def test_scripts_are_mentioned(self) -> None:
        d = self.add_skill("alpha", "x")
        (d / "scripts").mkdir()
        (d / "scripts/run.sh").write_text("echo hi\n")
        self.approve()
        code, out, _ = self.run_cli(["t"])
        self.assertEqual(code, 0)
        self.assertIn(".tink/.active/alpha/scripts", out)


class TestNoSkill(UseFlowCase):
    MSG = "No specialist skill applies to this task; proceed without one.\n"

    def test_uncertain_and_no_skill_needed(self) -> None:
        self.add_skill("alpha")
        self.approve()
        for status in ("uncertain", "no_skill_needed", "no_match"):
            code, out, _ = self.run_cli(["t"], self.router(status=status))
            self.assertEqual((code, out), (1, self.MSG), status)
        self.assertFalse((self.proj / ".tink").exists())

    def test_no_candidates_is_no_skill(self) -> None:
        self.add_skill("alpha")
        self.pin("build-skillset", ["alpha"], required=["alpha"])
        code, out, _ = self.run_cli(["--skillset", "build-skillset", "t"])
        self.assertEqual((code, out), (1, "No specialist skill on the build-skillset shelf applies to this "
                                          "task; proceed without one.\n"))
        self.assertEqual(self.seen, [])


class TestRefusals(UseFlowCase):
    def test_unapproved_leaks_nothing(self) -> None:
        self.add_skill("alpha", "TOP-SECRET-CONTENT")
        code, out, err = self.run_cli(["t"])
        self.assertEqual(code, 2)
        self.assertEqual(
            out, "Skill 'alpha' was selected but could not be delivered: it is not approved "
                 "(review it, then run `tink library approve alpha`); proceed without it.\n")
        self.assertNotIn("TOP-SECRET", out + err)
        self.assertFalse((self.proj / ".tink/.active/alpha").exists())

    def test_symlinked_skill_md_refused(self) -> None:
        d = self.home / "skills/alpha"
        d.mkdir()
        outside = self.tmp / "outside.md"
        outside.write_text(skill_md("alpha", "alpha specialist", "LEAKED-OUTSIDE"))
        os.symlink(outside, d / "SKILL.md")
        self.approve()
        code, out, err = self.run_cli(["t"])
        self.assertEqual(code, 2)
        self.assertIn("could not be delivered", out)
        self.assertNotIn("LEAKED-OUTSIDE", out + err)

    def test_tamper_after_approval_refused(self) -> None:
        d = self.add_skill("alpha", "original")
        self.approve()
        (d / "SKILL.md").write_text(skill_md("alpha", "alpha specialist", "TAMPERED"))
        code, out, _ = self.run_cli(["t"])
        self.assertEqual(code, 2)
        self.assertNotIn("TAMPERED", out)

    def test_tink_missing_fails_open(self) -> None:
        self.add_skill("alpha")
        self.approve()
        empty = self.tmp / "empty"
        empty.mkdir()
        with patch.dict(os.environ, {"PATH": str(empty)}):
            code, out, _ = self.run_cli(["t"])
        self.assertEqual(code, 2)
        self.assertEqual(
            out, "Skill 'alpha' was selected but could not be delivered: the `tink` CLI is not on PATH "
                 "(install it or fix PATH); proceed without it.\n")

    def test_router_failure_fails_open(self) -> None:
        self.add_skill("alpha")
        def boom(task, skills, args):
            raise RuntimeError("network down")
        code, out, _ = self.run_cli(["t"], boom)
        self.assertEqual(code, 2)
        self.assertIn("proceed without", out)
        self.assertNotIn("Traceback", out)

    def test_missing_api_key_fails_open_without_seam(self) -> None:
        self.add_skill("alpha")
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            os.environ.pop("TYPESAFE_API_KEY", None)
            code = cli.main(["t"])
        self.assertEqual(code, 2)
        self.assertIn("proceed without", out.getvalue())


class TestCandidates(UseFlowCase):
    def setUp(self) -> None:
        super().setUp()
        for n in ("alpha", "beta", "principle-x", "gamma"):
            self.add_skill(n)
        self.approve()

    def test_no_skillset_offers_whole_library(self) -> None:
        self.run_cli(["t"])
        self.assertEqual(sorted(self.seen[0]), ["alpha", "beta", "gamma", "principle-x"])

    def test_required_excluded_and_skillset_constrains(self) -> None:
        self.pin("build-skillset", ["alpha", "principle-x", "beta"], required=["principle-x"])
        self.run_cli(["--skillset", "build-skillset", "t"])
        self.assertEqual(sorted(self.seen[0]), ["alpha", "beta"])

    def test_skillset_flag_and_absent_required(self) -> None:
        self.pin("mine-skillset", ["alpha", "principle-x"])
        self.run_cli(["--skillset", "mine", "t"])
        self.assertEqual(sorted(self.seen[0]), ["alpha", "principle-x"])

    def test_unknown_skillset_fails_open(self) -> None:
        code, out, _ = self.run_cli(["--skillset", "nope", "t"])
        self.assertEqual(code, 2)
        self.assertIn("proceed without", out)
        self.assertEqual(self.seen, [])


class TestUsage(UseFlowCase):
    def usage_error(self, argv, needle):
        code, out, err = self.run_cli(argv)
        self.assertEqual(code, 2, argv)
        self.assertEqual(out, "")
        lines = err.splitlines()
        self.assertEqual(len(lines), 2, err)
        self.assertIn(needle, lines[0])
        self.assertEqual(self.seen, [])

    def test_empty_task(self) -> None:
        self.usage_error([], "task")
        self.usage_error(["   "], "task")

    def test_strict_flag_is_gone(self) -> None:
        self.usage_error(["--strict", "some task"], "--strict")
        self.usage_error(["--skillset", "x", "--strict", "some task"], "--strict")

    def test_anywhere_conflicts_with_skillset(self) -> None:
        self.usage_error(["--anywhere", "--skillset", "x", "some task"], "--anywhere")

    def test_negative_inline_max(self) -> None:
        self.usage_error(["--inline-max", "-1", "some task"], "--inline-max")

    def test_former_command_words_are_ordinary_tasks(self) -> None:
        self.add_skill("alpha")
        self.approve()
        for word in ("prune", "update", "version"):
            code, _, _ = self.run_cli([word])
            self.assertEqual(code, 0, word)
        self.assertEqual(self.tasks, ["prune", "update", "version"])


class TestPick(UseFlowCase):
    """`--pick` decides only: no mount, no writes, no receipt."""

    PICK_FIELDS = {"contract_version", "status", "task", "skillset", "scope", "specialist_noul",
                   "winner", "probability", "confidence", "threshold", "runner_up",
                   "runner_up_probability", "margin", "elapsed_ms", "fits", "shortlist"}

    def setUp(self) -> None:
        super().setUp()
        for n in ("alpha", "beta"):
            self.add_skill(n)  # deliberately NOT approved: pick must not need approval
        self.pin("build-skillset", ["alpha"])

    def snapshot(self) -> dict:
        snap = {}
        for path in sorted(self.tmp.rglob("*")):
            rel = str(path.relative_to(self.tmp))
            snap[rel] = None if path.is_dir() else path.read_bytes()
        return snap

    def test_pick_never_writes_anything(self) -> None:
        receipt = self.tmp / "r.jsonl"
        before = self.snapshot()
        with patch.dict(os.environ, {"TINK_ROUTE_RECEIPT": str(self.tmp / "env.jsonl")}):
            for extra in ([], ["--json"], ["--skillset", "build"]):
                code, _, _ = self.run_cli(["--pick", *extra, "t"])
                self.assertEqual(code, 0, extra)
        self.run_cli(["--pick", "t"], self.router(status="uncertain"))
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.proj / ".tink").exists())

    def test_pick_rejects_an_explicit_receipt(self) -> None:
        code, out, err = self.run_cli(["--pick", "--receipt", str(self.tmp / "r.jsonl"), "t"])
        self.assertEqual((code, out), (2, ""))
        lines = err.splitlines()
        self.assertEqual(len(lines), 2, err)
        self.assertIn("--receipt", lines[0])
        self.assertEqual(self.seen, [])
        self.assertFalse((self.tmp / "r.jsonl").exists())

    def test_pick_does_not_call_tink(self) -> None:
        with patch.dict(os.environ, {"PATH": "/nonexistent"}):
            code, out, _ = self.run_cli(["--pick", "t"])
        self.assertEqual(code, 0)
        self.assertEqual(out, "Skill: alpha (confidence 0.91)\n")

    def test_human_lines_and_exit_codes(self) -> None:
        code, out, _ = self.run_cli(["--pick", "t"])
        self.assertEqual((code, out), (0, "Skill: alpha (confidence 0.91)\n"))
        code, out, _ = self.run_cli(["--pick", "t"], self.router(status="no_skill_needed"))
        self.assertEqual((code, out), (1, "No specialist skill applies to this task.\n"))

    def test_json_field_set_is_exact(self) -> None:
        code, out, _ = self.run_cli(["--pick", "--json", "t"])
        d = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual(set(d), self.PICK_FIELDS)
        self.assertEqual((d["contract_version"], d["status"], d["winner"], d["scope"], d["skillset"]),
                         (1, "routed", "alpha", "library", None))
        self.assertEqual(d["confidence"], 0.91)

    def test_json_field_set_when_no_skill(self) -> None:
        code, out, _ = self.run_cli(["--pick", "--json", "t"], self.router(status="uncertain"))
        d = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(set(d), self.PICK_FIELDS | {"hint"})
        self.assertEqual((d["status"], d["winner"], d["hint"]), ("uncertain", None, None))

    def test_json_field_set_when_nothing_to_route(self) -> None:
        self.pin("empty-skillset", ["ghost"])
        code, out, _ = self.run_cli(["--pick", "--json", "--skillset", "empty", "t"])
        d = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(set(d), self.PICK_FIELDS | {"hint"})
        self.assertEqual((d["status"], d["scope"], d["skillset"]), ("no_candidates_available", "skillset", "empty"))
        self.assertEqual(self.seen, [["alpha", "beta"]])  # only the hint call: the shelf itself is empty
        self.assertEqual(d["hint"]["skill"], "alpha")

    def test_pick_uses_the_strict_shelf_and_hints(self) -> None:
        results = iter([("no_skill_needed", None), ("routed", "beta")])

        def fn(task, skills, args):
            self.seen.append([s["name"] for s in skills])
            status, winner = next(results)
            return RoutingResult(status=status, task=task, winner=winner, confidence=0.8, probability=0.8)
        before = self.snapshot()
        with patch.dict(os.environ, {"PATH": "/nonexistent"}):
            code, out, _ = self.run_cli(["--pick", "--json", "--skillset", "build-skillset", "t"], fn)
        d = json.loads(out)
        self.assertEqual((code, d["status"], d["winner"], d["scope"], d["skillset"]),
                         (1, "no_skill_needed", None, "skillset", "build-skillset"))
        self.assertEqual(d["hint"], {"skill": "beta", "shelves": []})
        self.assertEqual(self.seen, [["alpha"], ["beta"]])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.proj / ".tink").exists())

    def test_pick_human_prints_hint_line(self) -> None:
        results = iter([("no_skill_needed", None), ("routed", "beta")])

        def fn(task, skills, args):
            status, winner = next(results)
            return RoutingResult(status=status, task=task, winner=winner, confidence=0.8, probability=0.8)
        code, out, _ = self.run_cli(["--pick", "--skillset", "build-skillset", "t"], fn)
        self.assertEqual((code, out),
                         (1, "No specialist skill applies to this task.\n"
                             "Hint: beta fits but is not on any stage shelf; it was not delivered.\n"))

    def test_pick_errors_exit_2(self) -> None:
        code, out, _ = self.run_cli(["--pick", "--skillset", "nope", "t"])
        self.assertEqual(code, 2)
        self.assertIn("could not be resolved", out + _)
        code, out, _ = self.run_cli(["--pick", "--json", "--skillset", "nope", "t"])
        d = json.loads(out)
        self.assertEqual((code, d["status"], d["reason"]), (2, "error", "skillset_error"))

    def test_pick_rejects_unknown_winner(self) -> None:
        code, out, _ = self.run_cli(["--pick", "--json", "t"], self.router(winner="ghost"))
        self.assertEqual((code, json.loads(out)["reason"]), (2, "invalid_winner"))


class TestJson(UseFlowCase):
    def test_delivered_inline(self) -> None:
        self.add_skill("alpha", "body")
        self.approve()
        code, out, _ = self.run_cli(["--json", "t"])
        d = json.loads(out)
        content = (self.home / "skills/alpha/SKILL.md").read_text()
        self.assertEqual(code, 0)
        self.assertEqual(d, {
            "contract_version": 1, "status": "delivered", "skill": "alpha",
            "tree_digest": self.real_digest("alpha"), "chars": len(content),
            "delivery": "inline", "path": None, "confidence": 0.91,
            "content": content, "reason": None, "scope": "library", "hint": None})

    def test_delivered_path(self) -> None:
        self.add_skill("alpha", "body " * 100)
        self.approve()
        code, out, _ = self.run_cli(["--json", "--inline-max", "10", "t"])
        d = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((d["delivery"], d["path"], d["content"]),
                         ("path", ".tink/.active/alpha/SKILL.md", None))

    def test_no_skill(self) -> None:
        self.add_skill("alpha")
        code, out, _ = self.run_cli(["--json", "t"], self.router(status="uncertain"))
        d = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(d["status"], "no_skill")
        self.assertEqual(d["delivery"], "none")
        self.assertIsNone(d["content"])
        self.assertEqual(d["reason"], "uncertain")
        self.assertEqual(set(d), {"contract_version", "status", "skill", "tree_digest", "chars",
                                  "delivery", "path", "confidence", "content", "reason", "scope", "hint"})
        self.assertIsNone(d["hint"])

    def test_error_has_no_content(self) -> None:
        self.add_skill("alpha", "SECRET")
        code, out, _ = self.run_cli(["--json", "t"])
        d = json.loads(out)
        self.assertEqual(code, 2)
        self.assertEqual((d["status"], d["skill"], d["reason"], d["delivery"], d["content"]),
                         ("error", "alpha", "unapproved", "none", None))
        self.assertNotIn("SECRET", out)


class TestReceipts(UseFlowCase):
    def test_lines_append_only_across_calls(self) -> None:
        self.add_skill("alpha")
        self.approve()
        rp = self.tmp / "deep/dir/receipts.jsonl"
        self.run_cli(["--receipt", str(rp), "first"])
        self.run_cli(["--receipt", str(rp), "second"], self.router(status="uncertain"))
        (self.home / "skills/alpha/SKILL.md").write_text(skill_md("alpha", "alpha specialist", "changed"))
        self.run_cli(["--receipt", str(rp), "third", "--skillset", "x"])
        lines = self.receipt_lines(rp)
        # third: unknown skillset => error before routing
        self.assertEqual([l["task"] for l in lines], ["first", "second", "third"])
        self.assertEqual([l["status"] for l in lines], ["delivered", "no_skill", "error"])
        keys = {"ts", "task", "skillset", "status", "skill", "tree_digest", "chars",
                "delivery", "confidence", "reason", "scope", "hint_skill"}
        for l in lines:
            self.assertEqual(set(l), keys)
        self.assertEqual(lines[0]["skill"], "alpha")
        self.assertEqual(lines[0]["delivery"], "inline")
        self.assertTrue(lines[0]["ts"].endswith("Z"))
        self.assertEqual(lines[1]["reason"], "uncertain")
        self.assertEqual(lines[2]["skillset"], "x")
        self.assertFalse((self.tmp / "deep/dir/receipts.jsonl.lock").exists())
        self.assertEqual(sorted(p.name for p in rp.parent.iterdir()), ["receipts.jsonl"])

    def test_env_var(self) -> None:
        self.add_skill("alpha")
        self.approve()
        rp = self.tmp / "r.jsonl"
        with patch.dict(os.environ, {"TINK_ROUTE_RECEIPT": str(rp)}):
            self.run_cli(["t"])
        self.assertEqual(len(self.receipt_lines(rp)), 1)

    def test_mount_error_recorded(self) -> None:
        self.add_skill("alpha")
        rp = self.tmp / "r.jsonl"
        self.run_cli(["--receipt", str(rp), "t"])
        l = self.receipt_lines(rp)[0]
        self.assertEqual((l["status"], l["skill"], l["reason"]), ("error", "alpha", "unapproved"))

    def test_symlinked_receipt_refused(self) -> None:
        self.add_skill("alpha")
        self.approve()
        target = self.tmp / "victim.txt"
        target.write_text("keep\n")
        rp = self.tmp / "link.jsonl"
        os.symlink(target, rp)
        code, out, err = self.run_cli(["--receipt", str(rp), "t"])
        self.assertEqual(code, 0)
        self.assertIn("Apply these instructions", out)
        self.assertEqual(target.read_text(), "keep\n")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("receipt", err)

    def test_unwritable_receipt_warns_only(self) -> None:
        self.add_skill("alpha")
        self.approve()
        code0, out0, _ = self.run_cli(["t"])
        rp = self.tmp / "isdir"
        rp.mkdir()
        code, out, err = self.run_cli(["--receipt", str(rp), "t"])
        self.assertEqual((code, out), (code0, out0))
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("receipt", err)

    def test_no_stray_files(self) -> None:
        self.add_skill("alpha")
        self.approve()
        self.run_cli(["t"])
        top = sorted(p.name for p in self.proj.iterdir() if p.name != ".git")
        self.assertEqual(top, [".tink"] if (self.proj / ".tink").exists() else [])


class TestPlainMessages(UseFlowCase):
    """User-facing failure text: plain sentence plus the fix; slug stays in --json and receipts."""

    def failrun(self, argv, route_fn=None, env=None):
        with patch.dict(os.environ, env or {}):
            return self.run_cli(argv, route_fn)

    def check_slug(self, argv, slug, expected, route_fn=None, env=None):
        code, out, _ = self.failrun(argv, route_fn, env)
        self.assertEqual((code, out), (2, expected), slug)
        code, out, _ = self.failrun(argv + ["--json"], route_fn, env)
        self.assertEqual(json.loads(out)["reason"], slug)

    def test_no_api_key(self) -> None:
        self.add_skill("alpha")
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.tmp / "no-config")}), \
                patch("tink_route.core.credentials._from_launchctl", return_value=None), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            os.environ.pop("TYPESAFE_API_KEY", None)
            code = cli.main(["t"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(),
                         "Skill routing unavailable: no TypeSafe API key was found (set TYPESAFE_API_KEY, or put "
                         "the key in ~/.config/tink-route/typesafe_api_key with mode 600); proceed without a skill.\n")

    def test_route_failed(self) -> None:
        self.add_skill("alpha")

        def boom(task, skills, args):
            raise RuntimeError("network down")
        self.check_slug(["t"], "route_failed",
                        "Skill routing unavailable: the routing service call failed (network or API error; "
                        "retry later); proceed without a skill.\n", boom)

    def test_library_missing(self) -> None:
        self.check_slug(["--library", str(self.tmp / "nope"), "t"], "library_missing",
                        "Skill routing unavailable: the skill library was not found (default ~/.tink-library/skills or "
                        "$TINK_HOME/skills; override with --library); proceed without a skill.\n")

    def test_library_unreadable(self) -> None:
        self.add_skill("alpha")
        with patch("tink_route.flow.load_library_skills", side_effect=OSError("x")):
            self.check_slug(["t"], "library_unreadable",
                            "Skill routing unavailable: the skill library could not be read (run `tink doctor`); "
                            "proceed without a skill.\n")

    def test_skillset_error(self) -> None:
        self.add_skill("alpha")
        self.check_slug(["--skillset", "nope", "t"], "skillset_error",
                        "Skill routing unavailable: the skillset could not be resolved (see `tink skillset list` or "
                        "check the pin under .tink/skillsets or $TINK_HOME/skillsets); proceed without a skill.\n")

    def test_unknown_slug_names_it(self) -> None:
        self.add_skill("alpha")
        self.check_slug(["t"], "invalid_winner",
                        "Skill 'ghost' was selected but could not be delivered (invalid_winner); "
                        "proceed without it.\n",
                        self.router(winner="ghost"))

    def test_digest_mismatch(self) -> None:
        d = self.add_skill("alpha", "original")
        self.approve()
        (d / "SKILL.md").write_text(skill_md("alpha", "alpha specialist", "TAMPERED"))
        code, out, _ = self.run_cli(["--json", "t"])
        reason = json.loads(out)["reason"]
        self.assertEqual(reason, "digest_mismatch")
        code, out, _ = self.run_cli(["t"])
        self.assertEqual(out, "Skill 'alpha' was selected but could not be delivered: it changed since "
                              "approval (review it, then run `tink library approve alpha`); "
                              "proceed without it.\n")

    def test_symlink_refused(self) -> None:
        d = self.home / "skills/alpha"
        d.mkdir()
        outside = self.tmp / "outside.md"
        outside.write_text(skill_md("alpha", "alpha specialist", "x"))
        os.symlink(outside, d / "SKILL.md")
        self.approve()
        _, out, _ = self.run_cli(["--json", "t"])
        slug = json.loads(out)["reason"]
        _, out, _ = self.run_cli(["t"])
        if slug == "symlink_refused":
            self.assertEqual(out, "Skill 'alpha' was selected but could not be delivered: a symlink was "
                                  "found in the skill (replace it with a real copy); proceed without it.\n")
        else:
            self.assertIn("could not be delivered", out)

    def test_unmapped_delivery_slug_generic(self) -> None:
        self.add_skill("alpha")
        self.approve()
        with patch("tink_route.flow._mount", side_effect=__import__("tink_route.flow", fromlist=["x"]).FlowError("weird_code")):
            _, out, _ = self.run_cli(["t"])
        self.assertEqual(out, "Skill 'alpha' was selected but could not be delivered (weird_code); "
                              "proceed without it.\n")

    def test_receipt_keeps_slug(self) -> None:
        self.add_skill("alpha")
        rp = self.tmp / "r.jsonl"
        self.run_cli(["--receipt", str(rp), "t"])
        self.assertEqual(self.receipt_lines(rp)[0]["reason"], "unapproved")


class TestReceiptLocking(UseFlowCase):
    def test_no_sidecar_and_file_perms(self) -> None:
        from tink_route.flow import _write_receipt
        rp = self.tmp / "sub/r.jsonl"
        _write_receipt(rp, {"a": 1})
        _write_receipt(rp, {"a": 2})
        self.assertEqual(sorted(p.name for p in rp.parent.iterdir()), ["r.jsonl"])
        self.assertEqual(len(rp.read_text().splitlines()), 2)
        self.assertEqual(rp.stat().st_mode & 0o777, 0o644 & ~os.umask(0))

    def test_flock_taken_on_receipt_descriptor(self) -> None:
        from tink_route import flow
        rp = self.tmp / "r.jsonl"
        with patch.object(flow.fcntl, "flock") as fl:
            flow._write_receipt(rp, {"a": 1})
        self.assertEqual(fl.call_count, 2)  # LOCK_EX then LOCK_UN



BEGIN = "<!-- tink:rules begin skillset={name} digest=abc123 -->"
END = "<!-- tink:rules end -->"
NO_SKILL = "No specialist skill applies to this task; proceed without one.\n"


def block(name: str, inner: str = "Rules go here.") -> str:
    return f"# Project\n\nSome prose.\n\n{BEGIN.format(name=name)}\n{inner}\n{END}\n\nMore prose.\n"


class ShelfCase(UseFlowCase):
    """The shelf comes from the tink:rules block in ./AGENTS.md; it is strict (no fallback)."""

    def setUp(self) -> None:
        super().setUp()
        for name in ("alpha", "beta", "gamma", "prin", "delta"):
            self.add_skill(name)
        self.pin("build-skillset", ["alpha", "prin"], required=["prin"])
        self.pin("test-skillset", ["beta", "gamma"])
        self.pin("also-skillset", ["gamma"])
        self.pin("zed-skillset", ["gamma"])
        self.approve()
        self.receipt = self.tmp / "receipt.jsonl"

    def agents(self, text: str) -> None:
        (self.proj / "AGENTS.md").write_text(text)

    def seq_router(self, results):
        calls = iter(results)

        def fn(task, skills, args):
            self.seen.append([s["name"] for s in skills])
            item = next(calls)
            if isinstance(item, Exception):
                raise item
            status, winner = item
            return RoutingResult(status=status, task=task, winner=winner if status == "routed" else None,
                                 confidence=0.9, probability=0.9)
        return fn

    def go(self, extra, results, receipt=True):
        argv = (["--receipt", str(self.receipt)] if receipt else []) + [*extra, "some task"]
        return self.run_cli(argv, route_fn=self.seq_router(results))

    def last_receipt(self) -> dict:
        return self.receipt_lines(self.receipt)[-1]


class TestBlockIsIgnored(ShelfCase):
    """Default routing searches the whole library. AGENTS.md is never read, so a stage's rules block, a
    ghost skillset or a malformed block change nothing. A shelf is only ever explicit (`--skillset`)."""

    ALL = ["alpha", "beta", "delta", "gamma", "prin"]

    def test_rules_block_does_not_scope_candidates(self) -> None:
        self.agents(block("build-skillset"))
        code, _, _ = self.go([], [("routed", "beta")])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.seen[0]), self.ALL)
        rec = self.last_receipt()
        self.assertEqual((rec["scope"], rec["skillset"]), ("library", None))

    def test_ghost_and_malformed_blocks_cannot_fail_routing(self) -> None:
        b, e = BEGIN.format(name="build-skillset"), END
        for label, text in {"ghost": block("ghost-skillset"), "begin only": f"{b}\nx\n", "end before begin": f"{e}\n{b}\n",
                            "duplicate": f"{b}\nx\n{e}\n{b}\ny\n{e}\n"}.items():
            with self.subTest(label):
                self.seen.clear()
                self.agents(text)
                code, out, _ = self.go([], [("routed", "beta")])
                self.assertEqual(code, 0, out)
                self.assertEqual(sorted(self.seen[0]), self.ALL)

    def test_explicit_skillset_is_still_a_strict_shelf(self) -> None:
        self.agents(block("build-skillset"))
        self.go(["--skillset", "test-skillset"], [("routed", "beta")])
        self.assertEqual(sorted(self.seen[0]), ["beta", "gamma"])
        self.assertEqual(self.last_receipt()["scope"], "skillset")

    def test_anywhere_is_the_default_spelled_out(self) -> None:
        self.agents(block("build-skillset"))
        code, _, _ = self.go(["--anywhere"], [("routed", "beta")])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.seen[0]), self.ALL)
        self.assertEqual(self.last_receipt()["scope"], "library")

    def test_no_agents_file_means_whole_library(self) -> None:
        self.go([], [("no_skill_needed", None)])
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(sorted(self.seen[0]), self.ALL)
        self.assertEqual(self.last_receipt()["scope"], "library")


class TestStrictShelfAndHint(ShelfCase):
    def setUp(self) -> None:
        super().setUp()
        self.shelf = ["--skillset", "build-skillset"]

    def go(self, extra, results, receipt=True):
        explicit = any(a in ("--skillset", "--anywhere") for a in extra)
        return super().go(extra if explicit else [*self.shelf, *extra], results, receipt)

    def test_empty_shelf_names_the_shelf_and_hints_over_the_rest(self) -> None:
        code, out, _ = self.go([], [("no_skill_needed", None), ("no_skill_needed", None)])
        self.assertEqual(code, 1)
        self.assertEqual(out, "No specialist skill on the build-skillset shelf applies to this task; "
                              "proceed without one.\n")
        self.assertEqual(self.seen[0], ["alpha"])
        # rest of the library: not this shelf's candidates, not its required
        self.assertEqual(sorted(self.seen[1]), ["beta", "delta", "gamma"])
        self.assertEqual(len(self.seen), 2)
        self.assertEqual(self.last_receipt()["scope"], "skillset")
        self.assertIsNone(self.last_receipt()["hint_skill"])

    def test_hint_is_printed_and_never_delivered(self) -> None:
        code, out, _ = self.go([], [("no_skill_needed", None), ("routed", "beta")])
        self.assertEqual(code, 1)
        self.assertEqual(out, "No specialist skill on the build-skillset shelf applies to this task; "
                              "proceed without one.\n"
                              "Hint: beta fits but is on another shelf (test-skillset); it was not delivered.\n")
        self.assertNotIn("Do the thing", out)
        self.assertFalse((self.proj / ".tink").exists())
        rec = self.last_receipt()
        self.assertEqual((rec["status"], rec["skill"], rec["hint_skill"], rec["scope"]),
                         ("no_skill", None, "beta", "skillset"))

    def test_hint_without_any_shelf(self) -> None:
        _, out, _ = self.go([], [("no_skill_needed", None), ("routed", "delta")])
        self.assertIn("Hint: delta fits but is not on any stage shelf; it was not delivered.\n", out)

    def test_hint_lists_at_most_two_sorted_shelves(self) -> None:
        _, out, _ = self.go([], [("no_skill_needed", None), ("routed", "gamma")])
        self.assertIn("Hint: gamma fits but is on another shelf (also-skillset, test-skillset); "
                      "it was not delivered.\n", out)

    def test_project_pins_are_listed_before_home_pins(self) -> None:
        d = self.proj / ".tink" / "skillsets"
        d.mkdir(parents=True)
        (d / "proj-skillset.json").write_text(json.dumps({"members": ["gamma"]}))
        _, out, _ = self.go([], [("no_skill_needed", None), ("routed", "gamma")])
        self.assertIn("(proj-skillset, also-skillset)", out)

    def test_malformed_pin_is_skipped_when_naming_shelves(self) -> None:
        (self.home / "skillsets" / "aaa-skillset.json").write_text("{nope")
        _, out, _ = self.go([], [("no_skill_needed", None), ("routed", "delta")])
        self.assertIn("not on any stage shelf", out)

    def test_hint_failure_is_silent(self) -> None:
        code, out, _ = self.go([], [("no_skill_needed", None), RuntimeError("boom")])
        self.assertEqual(code, 1)
        self.assertEqual(out, "No specialist skill on the build-skillset shelf applies to this task; "
                              "proceed without one.\n")

    def test_invalid_hint_winner_is_omitted(self) -> None:
        for bad in ("ghost", "alpha", "prin"):  # unknown, on this shelf, required
            with self.subTest(bad):
                self.seen.clear()
                _, out, _ = self.go([], [("no_skill_needed", None), ("routed", bad)])
                self.assertNotIn("Hint", out)

    def test_hint_skipped_when_nothing_is_left(self) -> None:
        self.pin("all-skillset", ["alpha", "beta", "gamma", "delta", "prin"])
        code, _, _ = self.go(["--skillset", "all-skillset"], [("no_skill_needed", None)])
        self.assertEqual(code, 1)
        self.assertEqual(len(self.seen), 1)

    def test_json_hint_shapes(self) -> None:
        _, out, _ = self.go(["--json"], [("no_skill_needed", None), ("routed", "gamma")])
        d = json.loads(out)
        self.assertEqual((d["status"], d["scope"]), ("no_skill", "skillset"))
        self.assertEqual(d["hint"], {"skill": "gamma", "shelves": ["also-skillset", "test-skillset"]})
        _, out, _ = self.go(["--json"], [("no_skill_needed", None), ("no_skill_needed", None)])
        self.assertIsNone(json.loads(out)["hint"])

    def test_routed_shelf_makes_exactly_one_call_and_no_hint(self) -> None:
        code, out, _ = self.go(["--json"], [("routed", "alpha")])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.seen), 1)
        self.assertIsNone(json.loads(out)["hint"])

    def test_shelf_error_does_not_hint(self) -> None:
        code, _, _ = self.go([], [RuntimeError("boom")])
        self.assertEqual(code, 2)
        self.assertEqual(len(self.seen), 1)

    def test_explicit_skillset_is_equally_strict(self) -> None:
        code, out, _ = self.go(["--skillset", "test-skillset"], [("uncertain", None), ("routed", "alpha")])
        self.assertEqual(code, 1)
        self.assertIn("on the test-skillset shelf", out)
        self.assertIn("Hint: alpha fits but is on another shelf (build-skillset)", out)
        self.assertEqual(len(self.seen), 2)

    def test_no_shelf_never_hints(self) -> None:
        code, out, _ = self.go(["--anywhere"], [("no_skill_needed", None), ("routed", "beta")])
        self.assertEqual((code, out), (1, NO_SKILL))
        self.assertEqual(len(self.seen), 1)
        _, out, _ = self.go(["--anywhere", "--json"], [("no_skill_needed", None)])
        self.assertIsNone(json.loads(out)["hint"])

    def test_empty_shelf_all_required_still_hints(self) -> None:
        self.pin("req-skillset", ["alpha"], required=["alpha"])
        code, out, _ = self.go(["--skillset", "req-skillset"], [("routed", "beta")])
        self.assertEqual(code, 1)
        self.assertEqual(self.seen, [["beta", "delta", "gamma", "prin"]])
        self.assertIn("No specialist skill on the req-skillset shelf", out)
        self.assertIn("Hint: beta fits", out)

    def test_pick_with_a_skillset_uses_the_shelf(self) -> None:
        code, out, _ = self.go(["--pick"], [("routed", "alpha")], receipt=False)
        self.assertEqual((code, out), (0, "Skill: alpha (confidence 0.90)\n"))
        self.assertEqual(self.seen, [["alpha"]])


class TestProjectPins(UseFlowCase):
    """Skillset pins live in the project (`.tink/skillsets/`) and win over home pins."""

    def setUp(self) -> None:
        super().setUp()
        for name in ("alpha", "beta", "gamma", "prin", "hprin"):
            self.add_skill(name)
        self.approve()
        self.receipt = self.tmp / "receipt.jsonl"

    def project_pin(self, filename: str, members, required=None, raw: str | None = None) -> Path:
        d = self.proj / ".tink" / "skillsets"
        d.mkdir(parents=True, exist_ok=True)
        data = {"source": "x", "revision": "r", "sourceRoot": "s", "members": members}
        if required is not None:
            data["required"] = required
        path = d / filename
        path.write_text(raw if raw is not None else json.dumps(data))
        return path

    def deliver(self, extra, results=None):
        results = list(results or [("routed", "alpha")])
        calls = iter(results)

        def fn(task, skills, args):
            self.seen.append([s["name"] for s in skills])
            status, winner = next(calls)
            return RoutingResult(status=status, task=task, winner=winner if status == "routed" else None,
                                 confidence=0.9, probability=0.9)
        return self.run_cli(["--receipt", str(self.receipt), *extra, "some task"], route_fn=fn)

    def test_project_pin_resolves_without_home_pin(self) -> None:
        self.project_pin("build-skillset.json", ["alpha", "beta", "prin"], required=["prin"])
        code, _, _ = self.deliver(["--skillset", "build-skillset"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.seen[0]), ["alpha", "beta"])

    def test_project_pin_wins_over_differing_home_pin(self) -> None:
        self.pin("build-skillset", ["gamma", "hprin"], required=["gamma"])
        self.project_pin("build-skillset.json", ["alpha", "beta", "prin"], required=["prin"])
        code, _, _ = self.deliver(["--skillset", "build-skillset"])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(self.seen[0]), ["alpha", "beta"])

    def test_bare_name_project_pin_and_canonical_request(self) -> None:
        self.project_pin("mine.json", ["alpha", "gamma"])
        self.deliver(["--skillset", "mine"])
        self.deliver(["--skillset", "mine-skillset"])
        self.assertEqual(sorted(self.seen[0]), ["alpha", "gamma"])
        self.assertEqual(sorted(self.seen[1]), ["alpha", "gamma"])

    def test_canonical_project_pin_beats_bare_project_pin(self) -> None:
        self.project_pin("mine.json", ["gamma"])
        self.project_pin("mine-skillset.json", ["alpha"])
        self.deliver(["--skillset", "mine"])
        self.assertEqual(self.seen[0], ["alpha"])

    def test_malformed_project_pin_fails_closed(self) -> None:
        self.pin("build-skillset", ["gamma"])
        self.project_pin("build-skillset.json", [], raw="{not json")
        code, out, _ = self.deliver(["--skillset", "build-skillset"])
        self.assertEqual(code, 2)
        self.assertIn("could not be resolved", out)
        self.assertEqual(self.seen, [])

    def test_project_pin_without_members_fails_closed(self) -> None:
        self.pin("build-skillset", ["gamma"])
        self.project_pin("build-skillset.json", [], raw='{"source": "x"}')
        code, out, _ = self.deliver(["--skillset", "build-skillset"])
        self.assertEqual(code, 2)
        self.assertEqual(self.seen, [])

    def test_missing_everywhere_mentions_project_location(self) -> None:
        code, out, _ = self.deliver(["--skillset", "nope"])
        self.assertEqual(code, 2)
        self.assertIn(".tink/skillsets", out)
        from tink_route.core.exceptions import SkillsetError
        from tink_route.metadata import resolve_skillset
        with self.assertRaises(SkillsetError) as cm:
            resolve_skillset("nope", self.home, self.proj)
        self.assertIn(".tink/skillsets", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
