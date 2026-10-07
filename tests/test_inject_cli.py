"""tink-inject end to end: the console command, driven like a harness hook, against a fake tink-route on PATH.

Ways this could fail, written before the move:
1. The `tink-inject` entry point is missing or crashes on import.
2. A failing or garbled router makes the hook exit non-zero or print invalid JSON, which breaks the agent.
3. `needs:` lines in a prompt deliver nothing, or deliver without the reference frame.
4. A need with no matching skill still injects text.
5. INJECT=off still injects.
6. The injector writes into the repository it runs in (it may only write under INJECT_HOME).
7. A malformed numeric setting crashes at import, before the fail-open boundary.
8. An unwritable log makes the failure handler itself crash.
9. A harness session_id with "/" or ".." escapes INJECT_HOME.
10. An inherited TINK_ROUTE_RECEIPT makes every injection write a tink-route receipt.
11. Guidance given at launch (`needs`/`plan`) is given again by the hook session in the same checkout.
12. The first in-flight Write/Edit is missed because the tree baseline is taken after the tool changed it.
13. Two overlapping hook processes for one session both deliver the same skill.
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

FAKE_ROUTER = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
task = args[-1]
mode = os.environ.get("FAKE_ROUTER", "ok")
if os.environ.get("TINK_ROUTE_RECEIPT"):
    open(os.environ["TINK_ROUTE_RECEIPT"], "a").write("receipt\n")
if os.environ.get("FAKE_SLEEP"):
    import time; time.sleep(float(os.environ["FAKE_SLEEP"]))
if mode == "crash":
    sys.exit(2)
if mode == "garbage":
    print("not json"); sys.exit(0)
if "nothing fits" in task:
    print(json.dumps({{"status": "no_skill", "skill": None, "reason": "no_match", "probability": 0.1, "confidence": 0.1}}))
    sys.exit(1)
if "--pick" in args:
    print(json.dumps({{"status": "routed", "winner": "root-cause", "probability": 0.95}}))
else:
    content = "---\nname: root-cause\ndescription: Apply when debugging.\n---\n# Root cause\n\nTRACE EVERY SYMPTOM.\n"
    print(json.dumps({{"status": "delivered", "skill": "root-cause", "delivery": "inline", "confidence": 0.95,
                      "probability": 0.95, "content": content}}))
'''


class InjectCliTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        router = bin_dir / "tink-route"
        router.write_text(FAKE_ROUTER.format(python=sys.executable))
        router.chmod(0o755)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git = shutil.which("git")
        for args in (["init", "-q"], ["-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"]):
            subprocess.run([git, *args], cwd=self.repo, check=True)
        self.home = self.root / "inject-home"
        self.env = {"PATH": f"{bin_dir}:{Path(git).parent}:/usr/bin:/bin", "PYTHONPATH": SRC,
                    "INJECT_HOME": str(self.home), "HOME": str(self.root / "home")}

    def tink_inject(self, *argv, stdin="", **env):
        return subprocess.run([sys.executable, "-c", "from tink_route.inject import cli; cli()", *argv],
                              input=stdin, capture_output=True, text=True, cwd=self.repo,
                              env={**self.env, **env}, timeout=60)

    def prompt_event(self, prompt, session="s1"):
        return json.dumps({"hook_event_name": "UserPromptSubmit", "session_id": session,
                           "cwd": str(self.repo), "prompt": prompt})

    def test_prompt_needs_are_delivered_with_the_reference_frame(self):
        result = self.tink_inject("hook", stdin=self.prompt_event("Fix it.\nneeds: find the root cause of a bug"))
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn("Reference guidance", context)
        self.assertIn("TRACE EVERY SYMPTOM", context)
        self.assertNotIn("name: root-cause", context)  # front matter is stripped
        for word in ("root-cause", "skill", "library"):  # the agent never learns that skills exist
            self.assertNotIn(word, context.lower())

    def test_unmatched_need_injects_nothing(self):
        result = self.tink_inject("hook", stdin=self.prompt_event("needs: nothing fits this"))
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_a_broken_router_never_breaks_the_hook(self):
        for mode in ("crash", "garbage"):
            with self.subTest(mode=mode):
                result = self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug",
                                                                          session=mode), FAKE_ROUTER=mode)
                self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_inject_off_injects_nothing(self):
        result = self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug"), INJECT="off")
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_needs_command_prints_guidance_for_any_prompt(self):
        result = self.tink_inject("needs", "find the root cause of a bug")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TRACE EVERY SYMPTOM", result.stdout)

    def test_malformed_settings_fall_back_to_defaults(self):
        for name in ("INJECT_MAX_SKILLS", "INJECT_MIN_CONF", "INJECT_MAX_CHARS"):
            with self.subTest(setting=name):
                result = self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug",
                                                                          session=name), **{name: "bad"})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("TRACE EVERY SYMPTOM", result.stdout)

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
                self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug", session=session))
        written = {p.relative_to(self.root) for p in self.root.rglob("*") if p.is_file()
                   and p.relative_to(self.root).parts[0] not in ("bin", "repo")}  # the repo is checked elsewhere
        self.assertTrue(written and all(p.parts[0] == "inject-home" for p in written), written)
        self.assertFalse(outside.exists())

    def test_inherited_receipt_path_is_not_written(self):
        receipt = self.root / "receipt.jsonl"
        self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug"),
                         TINK_ROUTE_RECEIPT=str(receipt))
        self.assertFalse(receipt.exists())

    def test_launch_guidance_is_not_repeated_by_the_hook_session(self):
        self.assertIn("TRACE EVERY SYMPTOM", self.tink_inject("needs", "find the root cause of a bug").stdout)
        result = self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug", session="agent"))
        self.assertEqual((result.returncode, result.stdout), (0, ""))

    def test_first_in_flight_write_is_a_first_touch(self):
        (self.repo / "tests").mkdir()
        (self.repo / "tests" / "test_new.py").write_text("def test(): pass\n")
        event = json.dumps({"hook_event_name": "PostToolUse", "tool_name": "Write", "session_id": "w1",
                            "cwd": str(self.repo), "tool_input": {"file_path": str(self.repo / "tests" / "test_new.py")}})
        result = self.tink_inject("hook", stdin=event)
        self.assertIn("TRACE EVERY SYMPTOM", result.stdout)

    def test_overlapping_hooks_deliver_once(self):
        event = self.prompt_event("needs: find the root cause of a bug", session="race")
        procs = [subprocess.Popen([sys.executable, "-c", "from tink_route.inject import cli; cli()", "hook"],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, cwd=self.repo,
                                  env={**self.env, "FAKE_SLEEP": "0.5"}) for _ in range(2)]
        outputs = [p.communicate(event, timeout=60)[0] for p in procs]
        self.assertEqual(sum(1 for o in outputs if "TRACE EVERY SYMPTOM" in o), 1, outputs)

    def test_writes_only_under_inject_home(self):
        self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug"))
        self.tink_inject("needs", "find the root cause of a bug")
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.repo,
                                capture_output=True, text=True, env=self.env).stdout
        self.assertEqual(status, "")
        self.assertTrue((self.home / "log.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
