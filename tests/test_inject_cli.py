"""tink-inject end to end, driven like a harness hook.

Real: the tink-inject command, the Tink library and approvals file, approved-only routing, the threshold gate,
`tink mount` output parsing and the hook protocol. Faked: the routing service (a wrapper replaces
`flow._default_route` before running the command) and the `tink` binary (a script on PATH that checks approvals).

Ways this could fail, written before the code:
1. The entry point is missing or crashes on import.
2. A failing router, no key or unreadable approvals breaks the hook (non-zero exit, invalid JSON), or the failure
   is silent: nothing tells the operator that guidance is degraded.
3. The degradation notice reaches the agent's context (the agent must never learn that skills exist), repeats on
   every event, or prints the error message (which may contain the key).
4. `needs:` lines deliver nothing, deliver without the reference frame, or deliver an unapproved skill.
5. A need below INJECT_MIN_CONF is delivered, or is reported as degraded rather than as an abstention.
6. INJECT=off still injects.
7. The injector writes into the repository it runs in (it may only write under INJECT_HOME).
8. A malformed numeric setting crashes at import, before the fail-open boundary.
9. An unwritable log makes the failure handler itself crash.
10. A harness session_id with "/" or ".." escapes INJECT_HOME.
11. An inherited TINK_ROUTE_RECEIPT makes every injection write a tink-route receipt.
12. Guidance given at launch (`needs`/`plan`) is given again by the hook session in the same checkout.
13. The first in-flight Write/Edit is missed because the tree baseline is taken after the tool changed it.
14. Two overlapping hook processes for one session both deliver the same skill.
15. Slow routing runs past the harness's hook timeout and is killed with nothing logged.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SRC = str(Path(__file__).resolve().parents[1] / "src")
SENTINEL = "sk-SENTINEL-never-print-42"

WRAPPER = f'''
import os, sys, time
sys.path.insert(0, {SRC!r})
import tink_route.flow as flow
import tink_route.core.credentials as credentials
from tink_route.core.models import RoutingResult

credentials._from_launchctl = lambda: None  # tests must not pick up a real key from the machine

def fake_route(task, skills, args):
    if os.environ.get("FAKE_SLEEP"):
        time.sleep(float(os.environ["FAKE_SLEEP"]))
    mode = os.environ.get("FAKE_ROUTER", "ok")
    if mode == "crash":
        raise RuntimeError("upstream 401 for key {SENTINEL}")
    if mode == "nokey":
        raise flow.FlowError("no_api_key")
    if "nothing fits" in task:
        return RoutingResult(status="no_match", task=task)
    probability = float(os.environ.get("FAKE_PROB", "0.95"))
    if probability < args.threshold:
        return RoutingResult(status="uncertain", task=task, probability=probability)
    return RoutingResult(status="routed", task=task, winner=skills[0]["name"], probability=probability,
                         confidence=probability)

flow._default_route = fake_route
from tink_route.inject import cli
cli()
'''

FAKE_TINK = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
if args[:1] != ["mount"]:
    sys.exit(2)
name, home = args[1], os.environ["TINK_HOME"]
approvals = json.load(open(os.path.join(home, "approvals.json")))["skills"]
if name not in approvals:
    print(json.dumps({{"code": "unapproved"}})); sys.exit(1)
if "--payload" in args:
    content = open(os.path.join(home, "skills", name, "SKILL.md")).read()
    print(json.dumps({{"skill": name, "tree_digest": "sha256:" + "0" * 64,
                      "payload": {{"content": content, "chars": len(content)}}}}))
'''


class InjectCliTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        tink = bin_dir / "tink"
        tink.write_text(FAKE_TINK.format(python=sys.executable))
        tink.chmod(0o755)
        self.wrapper = self.root / "run_inject.py"
        self.wrapper.write_text(WRAPPER)
        self.tink_home = self.root / "tink-home"
        skill = self.tink_home / "skills" / "root-cause"
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: root-cause\ndescription: Apply when debugging.\n---\n"
                                        "# Root cause\n\nTRACE EVERY SYMPTOM.\n")
        (self.tink_home / "approvals.json").write_text(json.dumps({"version": 1, "skills": {"root-cause": "sha256:x"}}))
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git = shutil.which("git")
        for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"]):
            subprocess.run([git, *args], cwd=self.repo, check=True)
        self.home = self.root / "inject-home"
        self.env = {"PATH": f"{bin_dir}:{Path(git).parent}:/usr/bin:/bin", "INJECT_HOME": str(self.home),
                    "HOME": str(self.root / "home"), "TINK_HOME": str(self.tink_home)}

    def tink_inject(self, *argv, stdin="", **env):
        return subprocess.run([sys.executable, str(self.wrapper), *argv], input=stdin, capture_output=True, text=True,
                              cwd=self.repo, env={**self.env, **env}, timeout=60)

    def prompt_event(self, prompt, session="s1"):
        return json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session,
                           "cwd": str(self.repo), "prompt": prompt})

    def hook(self, prompt="needs: find the root cause of a bug", session="s1", **env):
        result = self.tink_inject("hook", stdin=self.prompt_event(prompt, session), **env)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result, (json.loads(result.stdout) if result.stdout.strip() else {})

    def context(self, out):
        return out.get("hookSpecificOutput", {}).get("additionalContext", "")

    # delivery -----------------------------------------------------------------------------------------------

    def test_prompt_needs_are_delivered_with_the_reference_frame(self):
        result, out = self.hook()
        self.assertIn("Reference guidance", self.context(out))
        self.assertIn("TRACE EVERY SYMPTOM", self.context(out))
        self.assertNotIn("name: root-cause", self.context(out))
        for word in ("root-cause", "skill", "library"):  # the agent never learns that skills exist
            self.assertNotIn(word, self.context(out).lower())
        self.assertNotIn("systemMessage", out)

    def test_unmatched_and_below_threshold_needs_abstain_quietly(self):
        for prompt, env in (("needs: nothing fits this", {}), ("needs: find the root cause of a bug", {"FAKE_PROB": "0.6"})):
            with self.subTest(prompt=prompt, env=env):
                result, out = self.hook(prompt, session=prompt + str(env), **env)
                self.assertEqual(out, {})  # an abstention is healthy: no guidance, no notice
                self.assertEqual(result.stderr, "")

    def test_only_approved_skills_are_routed(self):
        (self.tink_home / "approvals.json").write_text(json.dumps({"version": 1, "skills": {"other": "sha256:x"}}))
        _, out = self.hook()
        self.assertNotIn("TRACE EVERY SYMPTOM", self.context(out))

    def test_needs_command_prints_guidance_for_any_prompt(self):
        result = self.tink_inject("needs", "find the root cause of a bug")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TRACE EVERY SYMPTOM", result.stdout)

    def test_inject_off_injects_nothing(self):
        result, out = self.hook(INJECT="off")
        self.assertEqual(out, {})

    # degradation ----------------------------------------------------------------------------------------------

    def test_degradation_is_shown_to_the_operator_once_and_never_to_the_agent(self):
        cases = {"crash": ("route_failed", {"FAKE_ROUTER": "crash"}),
                 "nokey": ("no_api_key", {"FAKE_ROUTER": "nokey"}),
                 "approvals": ("approvals_unreadable", {})}
        for label, (reason, env) in cases.items():
            with self.subTest(case=label):
                if label == "approvals":
                    (self.tink_home / "approvals.json").unlink()
                result, out = self.hook(session=label, **env)
                self.assertNotIn("hookSpecificOutput", out)  # nothing in the agent's context
                self.assertIn(reason, out.get("systemMessage", ""))
                self.assertIn(reason, result.stderr)
                self.assertNotIn(SENTINEL, result.stdout + result.stderr)
                again, out2 = self.hook("needs: write tests that check behavior", session=label, **env)
                self.assertNotIn("systemMessage", out2)  # once per session per reason
        log = (self.home / "log.jsonl").read_text()
        self.assertNotIn(SENTINEL, log)
        self.assertIn('"degraded": true', log)

    def test_needs_command_reports_degradation_on_stderr_only(self):
        result = self.tink_inject("needs", "find the root cause of a bug", FAKE_ROUTER="nokey")
        self.assertEqual((result.returncode, result.stdout.strip()), (0, ""))
        self.assertIn("no_api_key", result.stderr)

    def test_slow_routing_stops_at_the_time_budget(self):
        prompt = "needs: find the root cause of a bug\nneeds: write tests that check behavior"
        result, out = self.hook(prompt, FAKE_SLEEP="1.2", INJECT_BUDGET="1")
        self.assertIn("budget_exhausted", out.get("systemMessage", ""))
        rows = [json.loads(line) for line in (self.home / "log.jsonl").read_text().splitlines()]
        self.assertTrue(any(r.get("reason") == "budget_exhausted" and r.get("degraded") for r in rows))

    # boundaries -----------------------------------------------------------------------------------------------

    def test_writes_only_under_inject_home(self):
        self.hook()
        self.tink_inject("needs", "find the root cause of a bug")
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.repo,
                                capture_output=True, text=True, env=self.env).stdout
        self.assertEqual(status, "")
        self.assertTrue((self.home / "log.jsonl").is_file())

    def test_malformed_settings_fall_back_to_defaults(self):
        for name in ("INJECT_MAX_SKILLS", "INJECT_MIN_CONF", "INJECT_MAX_CHARS", "INJECT_BUDGET"):
            with self.subTest(setting=name):
                _, out = self.hook(session=name, **{name: "bad"})
                self.assertIn("TRACE EVERY SYMPTOM", self.context(out))

    def test_an_unwritable_log_never_breaks_the_hook(self):
        blocker = self.root / "not-a-dir"
        blocker.write_text("x")
        result = self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug"),
                                  INJECT_LOG=str(blocker / "log.jsonl"), INJECT_HOME=str(blocker / "home"))
        self.assertEqual(result.returncode, 0)
        self.assertNotIn("Traceback", result.stderr)

    def test_session_ids_cannot_escape_inject_home(self):
        outside = self.root / "escaped"
        for session in (str(outside), "../../escaped-too", "a/b"):
            with self.subTest(session=session):
                self.hook(session=session)
        written = {p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()
                   and p.relative_to(self.root).parts[0] not in ("bin", "repo", "tink-home", "run_inject.py")}
        self.assertTrue(written and all(p.parts[0] == "inject-home" for p in written), written)
        self.assertFalse(outside.exists())

    def test_inherited_receipt_path_is_not_written(self):
        receipt = self.root / "receipt.jsonl"
        self.hook(TINK_ROUTE_RECEIPT=str(receipt))
        self.assertFalse(receipt.exists())

    def test_launch_guidance_is_not_repeated_by_the_hook_session(self):
        self.assertIn("TRACE EVERY SYMPTOM", self.tink_inject("needs", "find the root cause of a bug").stdout)
        _, out = self.hook(session="agent")
        self.assertEqual(out, {})

    def test_first_in_flight_write_is_a_first_touch(self):
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_new.py").write_text("def test(): pass\n")
        event = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write", "session_id": "w1",
                            "cwd": str(self.repo), "tool_input": {"file_path": str(self.repo / "tests" / "test_new.py")}})
        result = self.tink_inject("hook", stdin=event)
        self.assertIn("TRACE EVERY SYMPTOM", result.stdout)

    def test_overlapping_hooks_deliver_once(self):
        event = self.prompt_event("needs: find the root cause of a bug", session="race")
        procs = [subprocess.Popen([sys.executable, str(self.wrapper), "hook"], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, text=True, cwd=self.repo,
                                  env={**self.env, "FAKE_SLEEP": "0.5"}) for _ in range(2)]
        outputs = [p.communicate(event, timeout=60)[0] for p in procs]
        self.assertEqual(sum(1 for o in outputs if "TRACE EVERY SYMPTOM" in o), 1, outputs)


if __name__ == "__main__":
    unittest.main()
