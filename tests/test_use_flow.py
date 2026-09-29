"""In-process E2E for `tink-route --use`.

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
        code, out, _ = self.run_cli(["--use", "do alpha stuff"])
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
        code, out, _ = self.run_cli(["--use", "--inline-max", "100", "do alpha stuff"])
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
        _, out, _ = self.run_cli(["--use", "--inline-max", str(n), "t"])
        self.assertIn("Apply these instructions", out)
        _, out, _ = self.run_cli(["--use", "--inline-max", str(n - 1), "t"])
        self.assertNotIn("Apply these instructions", out)

    def test_scripts_are_mentioned(self) -> None:
        d = self.add_skill("alpha", "x")
        (d / "scripts").mkdir()
        (d / "scripts/run.sh").write_text("echo hi\n")
        self.approve()
        code, out, _ = self.run_cli(["--use", "t"])
        self.assertEqual(code, 0)
        self.assertIn(".tink/.active/alpha/scripts", out)


class TestNoSkill(UseFlowCase):
    MSG = "No specialist skill applies to this task; proceed without one.\n"

    def test_uncertain_and_no_skill_needed(self) -> None:
        self.add_skill("alpha")
        self.approve()
        for status in ("uncertain", "no_skill_needed", "no_match"):
            code, out, _ = self.run_cli(["--use", "t"], self.router(status=status))
            self.assertEqual((code, out), (1, self.MSG), status)
        self.assertFalse((self.proj / ".tink").exists())

    def test_no_candidates_is_no_skill(self) -> None:
        self.add_skill("alpha")
        self.pin("build-skillset", ["alpha"], required=["alpha"])
        code, out, _ = self.run_cli(["--use", "--stage", "build", "t"])
        self.assertEqual((code, out), (1, self.MSG))
        self.assertEqual(self.seen, [])


class TestRefusals(UseFlowCase):
    def test_unapproved_leaks_nothing(self) -> None:
        self.add_skill("alpha", "TOP-SECRET-CONTENT")
        code, out, err = self.run_cli(["--use", "t"])
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
        code, out, err = self.run_cli(["--use", "t"])
        self.assertEqual(code, 2)
        self.assertIn("could not be delivered", out)
        self.assertNotIn("LEAKED-OUTSIDE", out + err)

    def test_tamper_after_approval_refused(self) -> None:
        d = self.add_skill("alpha", "original")
        self.approve()
        (d / "SKILL.md").write_text(skill_md("alpha", "alpha specialist", "TAMPERED"))
        code, out, _ = self.run_cli(["--use", "t"])
        self.assertEqual(code, 2)
        self.assertNotIn("TAMPERED", out)

    def test_tink_missing_fails_open(self) -> None:
        self.add_skill("alpha")
        self.approve()
        empty = self.tmp / "empty"
        empty.mkdir()
        with patch.dict(os.environ, {"PATH": str(empty)}):
            code, out, _ = self.run_cli(["--use", "t"])
        self.assertEqual(code, 2)
        self.assertEqual(
            out, "Skill 'alpha' was selected but could not be delivered: the `tink` CLI is not on PATH "
                 "(install it or fix PATH); proceed without it.\n")

    def test_router_failure_fails_open(self) -> None:
        self.add_skill("alpha")
        def boom(task, skills, args):
            raise RuntimeError("network down")
        code, out, _ = self.run_cli(["--use", "t"], boom)
        self.assertEqual(code, 2)
        self.assertIn("proceed without", out)
        self.assertNotIn("Traceback", out)

    def test_missing_api_key_fails_open_without_seam(self) -> None:
        self.add_skill("alpha")
        out, err = io.StringIO(), io.StringIO()
        with patch.dict(os.environ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            os.environ.pop("TYPESAFE_API_KEY", None)
            code = cli.main(["--use", "t"])
        self.assertEqual(code, 2)
        self.assertIn("proceed without", out.getvalue())


class TestCandidates(UseFlowCase):
    def setUp(self) -> None:
        super().setUp()
        for n in ("alpha", "beta", "principle-x", "gamma"):
            self.add_skill(n)
        self.approve()

    def test_no_skillset_offers_whole_library(self) -> None:
        self.run_cli(["--use", "t"])
        self.assertEqual(sorted(self.seen[0]), ["alpha", "beta", "gamma", "principle-x"])

    def test_required_excluded_and_stage_constrains(self) -> None:
        self.pin("build-skillset", ["alpha", "principle-x", "beta"], required=["principle-x"])
        self.run_cli(["--use", "--stage", "build", "t"])
        self.assertEqual(sorted(self.seen[0]), ["alpha", "beta"])

    def test_skillset_flag_and_absent_required(self) -> None:
        self.pin("mine-skillset", ["alpha", "principle-x"])
        self.run_cli(["--use", "--skillset", "mine", "t"])
        self.assertEqual(sorted(self.seen[0]), ["alpha", "principle-x"])

    def test_unknown_skillset_fails_open(self) -> None:
        code, out, _ = self.run_cli(["--use", "--skillset", "nope", "t"])
        self.assertEqual(code, 2)
        self.assertIn("proceed without", out)
        self.assertEqual(self.seen, [])


class TestUsage(UseFlowCase):
    def test_incompatible_flags(self) -> None:
        for extra in (["-i"], ["--install"], ["--prune"], ["--multi"]):
            code, out, err = self.run_cli(["--use", *extra, "t"])
            self.assertEqual(code, 2, extra)
            self.assertEqual(out, "")
            self.assertEqual(len(err.strip().splitlines()), 1, extra)
            self.assertIn("--use", err)

    def test_empty_task(self) -> None:
        code, out, err = self.run_cli(["--use"])
        self.assertEqual(code, 2)
        self.assertEqual(len(err.strip().splitlines()), 1)

    def test_other_modes_unchanged(self) -> None:
        code, out, _ = self.run_cli(["version"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("tink-route "))


class TestJson(UseFlowCase):
    def test_delivered_inline(self) -> None:
        self.add_skill("alpha", "body")
        self.approve()
        code, out, _ = self.run_cli(["--use", "--json", "t"])
        d = json.loads(out)
        content = (self.home / "skills/alpha/SKILL.md").read_text()
        self.assertEqual(code, 0)
        self.assertEqual(d, {
            "contract_version": 1, "status": "delivered", "skill": "alpha",
            "tree_digest": self.real_digest("alpha"), "chars": len(content),
            "delivery": "inline", "path": None, "confidence": 0.91,
            "content": content, "reason": None, "scope": "library"})

    def test_delivered_path(self) -> None:
        self.add_skill("alpha", "body " * 100)
        self.approve()
        code, out, _ = self.run_cli(["--use", "--json", "--inline-max", "10", "t"])
        d = json.loads(out)
        self.assertEqual(code, 0)
        self.assertEqual((d["delivery"], d["path"], d["content"]),
                         ("path", ".tink/.active/alpha/SKILL.md", None))

    def test_no_skill(self) -> None:
        self.add_skill("alpha")
        code, out, _ = self.run_cli(["--use", "--json", "t"], self.router(status="uncertain"))
        d = json.loads(out)
        self.assertEqual(code, 1)
        self.assertEqual(d["status"], "no_skill")
        self.assertEqual(d["delivery"], "none")
        self.assertIsNone(d["content"])
        self.assertEqual(d["reason"], "uncertain")
        self.assertEqual(set(d), {"contract_version", "status", "skill", "tree_digest", "chars",
                                  "delivery", "path", "confidence", "content", "reason", "scope"})

    def test_error_has_no_content(self) -> None:
        self.add_skill("alpha", "SECRET")
        code, out, _ = self.run_cli(["--use", "--json", "t"])
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
        self.run_cli(["--use", "--receipt", str(rp), "first"])
        self.run_cli(["--use", "--receipt", str(rp), "second"], self.router(status="uncertain"))
        (self.home / "skills/alpha/SKILL.md").write_text(skill_md("alpha", "alpha specialist", "changed"))
        self.run_cli(["--use", "--receipt", str(rp), "third", "--skillset", "x"])
        lines = self.receipt_lines(rp)
        # third: unknown skillset => error before routing
        self.assertEqual([l["task"] for l in lines], ["first", "second", "third"])
        self.assertEqual([l["status"] for l in lines], ["delivered", "no_skill", "error"])
        keys = {"ts", "task", "skillset", "status", "skill", "tree_digest", "chars",
                "delivery", "confidence", "reason", "scope"}
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
            self.run_cli(["--use", "t"])
        self.assertEqual(len(self.receipt_lines(rp)), 1)

    def test_mount_error_recorded(self) -> None:
        self.add_skill("alpha")
        rp = self.tmp / "r.jsonl"
        self.run_cli(["--use", "--receipt", str(rp), "t"])
        l = self.receipt_lines(rp)[0]
        self.assertEqual((l["status"], l["skill"], l["reason"]), ("error", "alpha", "unapproved"))

    def test_symlinked_receipt_refused(self) -> None:
        self.add_skill("alpha")
        self.approve()
        target = self.tmp / "victim.txt"
        target.write_text("keep\n")
        rp = self.tmp / "link.jsonl"
        os.symlink(target, rp)
        code, out, err = self.run_cli(["--use", "--receipt", str(rp), "t"])
        self.assertEqual(code, 0)
        self.assertIn("Apply these instructions", out)
        self.assertEqual(target.read_text(), "keep\n")
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("receipt", err)

    def test_unwritable_receipt_warns_only(self) -> None:
        self.add_skill("alpha")
        self.approve()
        code0, out0, _ = self.run_cli(["--use", "t"])
        rp = self.tmp / "isdir"
        rp.mkdir()
        code, out, err = self.run_cli(["--use", "--receipt", str(rp), "t"])
        self.assertEqual((code, out), (code0, out0))
        self.assertEqual(len(err.strip().splitlines()), 1)
        self.assertIn("receipt", err)

    def test_no_stray_files(self) -> None:
        self.add_skill("alpha")
        self.approve()
        self.run_cli(["--use", "t"])
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
        with patch.dict(os.environ), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            os.environ.pop("TYPESAFE_API_KEY", None)
            code = cli.main(["--use", "t"])
        self.assertEqual(code, 2)
        self.assertEqual(out.getvalue(),
                         "Skill routing unavailable: TYPESAFE_API_KEY is not set (set it to enable skill routing); proceed without a skill.\n")

    def test_route_failed(self) -> None:
        self.add_skill("alpha")

        def boom(task, skills, args):
            raise RuntimeError("network down")
        self.check_slug(["--use", "t"], "route_failed",
                        "Skill routing unavailable: the routing service call failed (network or API error; "
                        "retry later); proceed without a skill.\n", boom)

    def test_library_missing(self) -> None:
        self.check_slug(["--use", "--library", str(self.tmp / "nope"), "t"], "library_missing",
                        "Skill routing unavailable: the skill library was not found (default ~/.tink-library/skills or "
                        "$TINK_HOME/skills; override with --library); proceed without a skill.\n")

    def test_library_unreadable(self) -> None:
        self.add_skill("alpha")
        with patch("tink_route.use.load_library_skills", side_effect=OSError("x")):
            self.check_slug(["--use", "t"], "library_unreadable",
                            "Skill routing unavailable: the skill library could not be read (run `tink doctor`); "
                            "proceed without a skill.\n")

    def test_skillset_error(self) -> None:
        self.add_skill("alpha")
        self.check_slug(["--use", "--skillset", "nope", "t"], "skillset_error",
                        "Skill routing unavailable: the skillset could not be resolved (see `tink skillset list` or "
                        "check the pin under $TINK_HOME/skillsets); proceed without a skill.\n")

    def test_unknown_slug_names_it(self) -> None:
        self.add_skill("alpha")
        self.check_slug(["--use", "t"], "invalid_winner",
                        "Skill 'ghost' was selected but could not be delivered (invalid_winner); "
                        "proceed without it.\n",
                        self.router(winner="ghost"))

    def test_digest_mismatch(self) -> None:
        d = self.add_skill("alpha", "original")
        self.approve()
        (d / "SKILL.md").write_text(skill_md("alpha", "alpha specialist", "TAMPERED"))
        code, out, _ = self.run_cli(["--use", "--json", "t"])
        reason = json.loads(out)["reason"]
        self.assertEqual(reason, "digest_mismatch")
        code, out, _ = self.run_cli(["--use", "t"])
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
        _, out, _ = self.run_cli(["--use", "--json", "t"])
        slug = json.loads(out)["reason"]
        _, out, _ = self.run_cli(["--use", "t"])
        if slug == "symlink_refused":
            self.assertEqual(out, "Skill 'alpha' was selected but could not be delivered: a symlink was "
                                  "found in the skill (replace it with a real copy); proceed without it.\n")
        else:
            self.assertIn("could not be delivered", out)

    def test_unmapped_delivery_slug_generic(self) -> None:
        self.add_skill("alpha")
        self.approve()
        with patch("tink_route.use._mount", side_effect=__import__("tink_route.use", fromlist=["x"]).UseError("weird_code")):
            _, out, _ = self.run_cli(["--use", "t"])
        self.assertEqual(out, "Skill 'alpha' was selected but could not be delivered (weird_code); "
                              "proceed without it.\n")

    def test_receipt_keeps_slug(self) -> None:
        self.add_skill("alpha")
        rp = self.tmp / "r.jsonl"
        self.run_cli(["--use", "--receipt", str(rp), "t"])
        self.assertEqual(self.receipt_lines(rp)[0]["reason"], "unapproved")


class TestReceiptLocking(UseFlowCase):
    def test_no_sidecar_and_file_perms(self) -> None:
        from tink_route.use import _write_receipt
        rp = self.tmp / "sub/r.jsonl"
        _write_receipt(rp, {"a": 1})
        _write_receipt(rp, {"a": 2})
        self.assertEqual(sorted(p.name for p in rp.parent.iterdir()), ["r.jsonl"])
        self.assertEqual(len(rp.read_text().splitlines()), 2)
        self.assertEqual(rp.stat().st_mode & 0o777, 0o644 & ~os.umask(0))

    def test_flock_taken_on_receipt_descriptor(self) -> None:
        from tink_route import use
        rp = self.tmp / "r.jsonl"
        with patch.object(use.fcntl, "flock") as fl:
            use._write_receipt(rp, {"a": 1})
        self.assertEqual(fl.call_count, 2)  # LOCK_EX then LOCK_UN


if __name__ == "__main__":
    unittest.main()


class TestStageFallback(UseFlowCase):
    """A stage skillset is a hint, not a wall: with nothing found there, retry over the whole library."""

    def setUp(self) -> None:
        super().setUp()
        for name in ("alpha", "beta", "gamma", "prin"):
            self.add_skill(name)
        self.pin("build-skillset", ["alpha", "prin"], required=["prin"])
        self.approve()
        self.receipt = self.tmp / "receipt.jsonl"

    def seq_router(self, results):
        calls = iter(results)

        def fn(task, skills, args):
            self.seen.append([s["name"] for s in skills])
            status, winner = next(calls)
            return RoutingResult(status=status, task=task, winner=winner if status == "routed" else None,
                                 confidence=0.9, probability=0.9)
        return fn

    def use(self, extra, results):
        return self.run_cli(["--use", "--receipt", str(self.receipt), *extra, "some task"],
                            route_fn=self.seq_router(results))

    def test_falls_back_to_library_when_stage_set_has_nothing(self) -> None:
        code, out, _ = self.use(["--stage", "build"], [("no_skill_needed", None), ("routed", "gamma")])
        self.assertEqual(code, 0)
        self.assertEqual(self.seen[0], ["alpha"])
        self.assertEqual(self.seen[1], ["alpha", "beta", "gamma"])
        self.assertNotIn("prin", self.seen[1])
        self.assertIn("# tink skill: gamma", out)
        self.assertIn("outside the build-skillset skillset", out)
        self.assertEqual(self.receipt_lines(self.receipt)[-1]["scope"], "library")

    def test_no_fallback_when_stage_set_routes(self) -> None:
        code, out, _ = self.use(["--stage", "build"], [("routed", "alpha")])
        self.assertEqual(code, 0)
        self.assertEqual(len(self.seen), 1)
        self.assertNotIn("outside the", out)
        self.assertEqual(self.receipt_lines(self.receipt)[-1]["scope"], "skillset")

    def test_stage_only_disables_fallback(self) -> None:
        code, out, _ = self.use(["--stage", "build", "--stage-only"], [("no_skill_needed", None)])
        self.assertEqual(code, 1)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.receipt_lines(self.receipt)[-1]["scope"], "skillset")

    def test_nothing_anywhere_is_exit_1_after_both_attempts(self) -> None:
        code, out, _ = self.use(["--stage", "build"], [("uncertain", None), ("no_skill_needed", None)])
        self.assertEqual(code, 1)
        self.assertEqual(len(self.seen), 2)
        self.assertIn("No specialist skill applies", out)
        self.assertEqual(self.receipt_lines(self.receipt)[-1]["scope"], "library")

    def test_unscoped_use_makes_a_single_library_call(self) -> None:
        code, _, _ = self.use([], [("no_skill_needed", None)])
        self.assertEqual(code, 1)
        self.assertEqual(len(self.seen), 1)
        self.assertEqual(self.receipt_lines(self.receipt)[-1]["scope"], "library")

    def test_json_reports_scope(self) -> None:
        code, out, _ = self.use(["--stage", "build", "--json"], [("no_skill_needed", None), ("routed", "gamma")])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)["scope"], "library")

    def test_stage_only_requires_a_scope(self) -> None:
        code, _, err = self.run_cli(["--use", "--stage-only", "some task"], route_fn=self.seq_router([]))
        self.assertEqual(code, 2)
        self.assertIn("--stage-only", err)
