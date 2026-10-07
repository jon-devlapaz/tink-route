"""tink-inject end to end: the console command, driven like a harness hook, against a fake tink-route on PATH.

Ways this could fail, written before the move:
1. The `tink-inject` entry point is missing or crashes on import.
2. A failing or garbled router makes the hook exit non-zero or print invalid JSON, which breaks the agent.
3. `needs:` lines in a prompt deliver nothing, or deliver without the reference frame.
4. A need with no matching skill still injects text.
5. INJECT=off still injects.
6. The injector writes into the repository it runs in (it may only write under INJECT_HOME).
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

    def test_writes_only_under_inject_home(self):
        self.tink_inject("hook", stdin=self.prompt_event("needs: find the root cause of a bug"))
        self.tink_inject("needs", "find the root cause of a bug")
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.repo,
                                capture_output=True, text=True, env=self.env).stdout
        self.assertEqual(status, "")
        self.assertTrue((self.home / "log.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
