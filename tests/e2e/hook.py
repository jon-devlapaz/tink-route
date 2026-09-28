#!/usr/bin/env python3
"""E2E: `tink-hook claude-code` ambient activation (route -> mount -> inject).

Every case runs the real hook entrypoint (`python3 -m tink_route.hook`) as a
child process with recorded Claude Code hook stdin, an isolated TINK_HOME and
HOME, a throwaway git project, and a PATH shim pointing `tink` at the
trust-checked build in the tink worktree. Deterministic cases use a fake router
configured through the user-scope `router_cmd`. Live cases (L*) need
TYPESAFE_API_KEY and are skipped without it.

Run:   python3 tests/e2e/hook.py [--only D1,D5]
Env:   TINK_BIN=/path/to/tink   (default: ../tink/target/debug/tink)
Artifact (repeatable, overwritten each run): target/e2e/hook.json
Exit:  0 all non-skipped cases pass, 1 any FAIL.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
ARTIFACT = REPO / "target" / "e2e" / "hook.json"
TINK_BIN = Path(os.environ.get("TINK_BIN", REPO.parent / "tink" / "target" / "debug" / "tink"))
REAL_LIBRARY = Path.home() / ".tink-library" / "skills"
LIVE_SKILLS = ["eli5", "tdd", "karpathy-guidelines", "how", "technical-writing"]
FRAMING_PREFIX = "tink:"
NOTICE_APPLIED = "tink: applied skill {} (disable: TINK_HOOK=off)"

FAKE_ROUTER = r'''
import json, os, sys, time
mode, arg, marker = sys.argv[1], sys.argv[2], sys.argv[3]
with open(marker, "a", encoding="utf-8") as f:
    f.write(json.dumps({"argv": sys.argv[4:], "pid": os.getpid()}) + "\n")
if mode == "routed":
    print(json.dumps({"contract_version": 1, "status": "routed", "winner": arg, "probability": 0.9,
                      "action": {"type": "inject", "mount_command": f"tink mount {arg} --json --payload"}}))
    sys.exit(0)
if mode == "multi":
    print(json.dumps({"contract_version": 1, "status": "multi_routed", "winner": arg,
                      "candidates": [{"skill": arg, "probability": 0.9}, {"skill": "other", "probability": 0.8}]}))
    sys.exit(0)
if mode == "badversion":
    print(json.dumps({"contract_version": 99, "status": "routed", "winner": arg}))
    sys.exit(0)
if mode == "noskill":
    print(json.dumps({"contract_version": 1, "status": "no_skill_needed"}))
    sys.exit(1)
if mode == "error":
    print(json.dumps({"error": "boom"}))
    sys.exit(2)
if mode == "garbage":
    print("<<<not json>>>")
    sys.exit(0)
if mode == "sleep":
    time.sleep(float(arg))
    with open(marker + ".finished", "w") as f:
        f.write("finished")
    print(json.dumps({"contract_version": 1, "status": "routed", "winner": "plain"}))
    sys.exit(0)
sys.exit(3)
'''


def skill_md(name, body="Body text.\n"):
    return f"---\nname: {name}\ndescription: Fixture skill {name} for hook e2e.\n---\n# {name}\n\n{body}"


class Env:
    def __init__(self, root: Path):
        self.root = root
        self.proj = root / "proj"
        self.home = root / "tinkhome"
        self.userhome = root / "userhome"
        self.bin = root / "bin"
        self.outside = root / "outside"
        self.marker = root / "router-calls.jsonl"
        for d in (self.proj, self.bin, self.outside, self.userhome):
            d.mkdir(parents=True)
        (self.bin / "tink").symlink_to(TINK_BIN)
        self.fake = root / "fake_router.py"
        self.fake.write_text(FAKE_ROUTER, encoding="utf-8")
        self.env = dict(os.environ)
        for k in ("TINK_HOOK", "TINK_HOOK_DEADLINE", "TINK_HOOK_MAX_CHARS", "TINK_HOOK_MOUNT_DEADLINE", "TINK_ROUTE_INSTALL"):
            self.env.pop(k, None)
        self.env.update(
            TINK_HOME=str(self.home),
            HOME=str(self.userhome),
            PATH=f"{self.bin}:{os.environ['PATH']}",
            PYTHONPATH=str(REPO / "src"),
        )
        self.run("git", "init", "-q", ".")
        self.run("git", "config", "user.email", "e2e@example.invalid")
        self.run("git", "config", "user.name", "e2e")
        self.run("tink", "init", "--no-tink-skills", "--no-manage-tink", "--no-sdlc")
        self.run("git", "add", "-A")
        self.run("git", "commit", "-q", "-m", "init")
        self.lib = self.home / "skills"
        self.lib.mkdir(parents=True, exist_ok=True)
        (self.outside / "secret.txt").write_text("TOP-SECRET ssh config\n")

    def run(self, *cmd, cwd=None, env=None, stdin=None):
        return subprocess.run(list(cmd), cwd=cwd or self.proj, env=env or self.env, input=stdin,
                              capture_output=True, text=True, check=False)

    def skill(self, name, *, body="Body text.\n", scripts=None, approve=True):
        d = self.lib / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(skill_md(name, body), encoding="utf-8")
        for rel, text in (scripts or {}).items():
            p = d / "scripts" / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8")
        if approve:
            r = self.run("tink", "library", "approve", name)
            assert r.returncode == 0, r.stderr
        return d

    def config(self, *, projects=None, router=None, write=True):
        cfg = {"version": 1, "projects": [str(self.proj.resolve())] if projects is None else projects,
               "router_cmd": router}
        if write:
            self.home.mkdir(parents=True, exist_ok=True)
            (self.home / "hook.json").write_text(json.dumps(cfg), encoding="utf-8")
        return cfg

    def fake_router(self, mode, arg="plain"):
        return [sys.executable, str(self.fake), mode, arg, str(self.marker)]

    def router_calls(self):
        if not self.marker.exists():
            return []
        return [json.loads(l) for l in self.marker.read_text(encoding="utf-8").splitlines() if l.strip()]

    def hook(self, stdin, *, args=("claude-code",), env_extra=None, cwd=None):
        env = dict(self.env, **(env_extra or {}))
        t0 = time.monotonic()
        r = subprocess.run([sys.executable, "-m", "tink_route.hook", *args], cwd=cwd or self.proj, env=env,
                           input=stdin, capture_output=True, text=True, check=False, timeout=60)
        elapsed = time.monotonic() - t0
        parsed = None
        if r.stdout.strip():
            try:
                parsed = json.loads(r.stdout)
            except json.JSONDecodeError:
                parsed = "UNPARSEABLE"
        return r, parsed, elapsed

    def prompt(self, text, *, session="sess-A", event="UserPromptSubmit", cwd=None, **extra):
        payload = {"session_id": session, "transcript_path": str(self.root / "t.jsonl"),
                   "cwd": str(cwd or self.proj), "permission_mode": "default",
                   "hook_event_name": event, "prompt": text}
        payload.update(extra)
        return json.dumps(payload)


def ctx_of(parsed):
    if not isinstance(parsed, dict):
        return None
    hso = parsed.get("hookSpecificOutput")
    if not isinstance(hso, dict):
        return None
    return hso.get("additionalContext")


def extract_block(ctx, name):
    m = re.search(r'<tink-skill name="' + re.escape(name) + r'" digest="(sha256:[0-9a-f]{64})">\n', ctx)
    if not m:
        return None, None
    end = ctx.rindex("\n</tink-skill>")
    return m.group(1), ctx[m.end():end]


def expect(cond, msg):
    if not cond:
        raise AssertionError(msg)


def assert_silent(r, parsed, what=""):
    expect(r.returncode == 0, f"{what} exit {r.returncode}, stderr={r.stderr[-400:]}")
    expect(r.stdout.strip() == "", f"{what} expected empty stdout, got {r.stdout[:300]!r}")


def assert_injected(env, r, parsed, name):
    expect(r.returncode == 0, f"exit {r.returncode} stderr={r.stderr[-400:]}")
    expect(isinstance(parsed, dict), f"stdout not JSON object: {r.stdout[:300]!r}")
    hso = parsed.get("hookSpecificOutput", {})
    expect(hso.get("hookEventName") == "UserPromptSubmit", f"hookEventName {hso.get('hookEventName')!r}")
    ctx = hso.get("additionalContext")
    expect(isinstance(ctx, str), "no additionalContext")
    expect(ctx.splitlines()[0].startswith(FRAMING_PREFIX), f"no framing line: {ctx[:120]!r}")
    digest, content = extract_block(ctx, name)
    expect(digest is not None, f"no <tink-skill name={name}> block")
    on_disk = (env.lib / name / "SKILL.md").read_text(encoding="utf-8")
    expect(content == on_disk, f"payload mismatch: {len(content or '')} vs {len(on_disk)} chars")
    expect(parsed.get("systemMessage") == NOTICE_APPLIED.format(name), f"notice {parsed.get('systemMessage')!r}")
    return digest, content


# ---------------------------------------------------------------- deterministic cases

def d1_routed(env):
    env.skill("plain", body="Always answer in haiku.\n")
    env.config(router=env.fake_router("routed", "plain"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_injected(env, r, p, "plain")
    expect(len(env.router_calls()) == 1, "router not invoked exactly once")
    return {"context_chars": len(ctx_of(p))}


def d2_multi_top1(env):
    env.skill("plain")
    env.skill("other", body="OTHER-SKILL-BODY\n")
    env.config(router=env.fake_router("multi", "plain"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_injected(env, r, p, "plain")
    expect("OTHER-SKILL-BODY" not in r.stdout, "runner-up leaked")


def d3_no_skill(env):
    env.config(router=env.fake_router("noskill"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_silent(r, p, "no-skill")
    expect(len(env.router_calls()) == 1, "router not invoked")


def d4_router_error(env):
    env.config(router=env.fake_router("error"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_silent(r, p, "router error")


def d5_timeout(env):
    env.skill("plain")
    env.config(router=env.fake_router("sleep", "20"))
    r, p, elapsed = env.hook(env.prompt("please write me a poem about autumn leaves"),
                             env_extra={"TINK_HOOK_DEADLINE": "1.0"})
    assert_silent(r, p, "timeout")
    expect(elapsed < 1.0 + 1.5, f"hook took {elapsed:.2f}s")
    calls = env.router_calls()
    expect(len(calls) == 1, "router not started")
    pid = calls[0]["pid"]
    time.sleep(0.3)
    alive = True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        alive = False
    expect(not alive, f"router child {pid} still alive after deadline")
    expect(not Path(str(env.marker) + ".finished").exists(), "router ran to completion")
    return {"elapsed_s": round(elapsed, 3)}


def d6_garbage(env):
    env.config(router=env.fake_router("garbage"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_silent(r, p, "garbage")


def d7_contract_mismatch(env):
    env.skill("plain")
    env.config(router=env.fake_router("badversion", "plain"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_silent(r, p, "contract mismatch")


def d8_unapproved(env):
    env.skill("plain", body="UNAPPROVED-MARKER-BODY\n", approve=False)
    env.config(router=env.fake_router("routed", "plain"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    expect(r.returncode == 0, "exit != 0")
    expect("UNAPPROVED-MARKER-BODY" not in r.stdout, "unapproved payload leaked")
    expect(ctx_of(p) is None, "additionalContext present for unapproved skill")
    expect(isinstance(p, dict) and p.get("systemMessage") == "tink: skill plain not applied (unapproved)",
           f"notice {r.stdout!r}")


def d9_symlinked_skill_md(env):
    d = env.lib / "linky"
    d.mkdir(parents=True)
    (d / "SKILL.md").symlink_to(env.outside / "secret.txt")
    env.run("tink", "library", "approve", "linky")
    env.config(router=env.fake_router("routed", "linky"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    expect(r.returncode == 0, "exit != 0")
    expect("TOP-SECRET" not in r.stdout, "symlink target leaked")
    expect(ctx_of(p) is None, "additionalContext present for symlinked skill")
    return {"stdout": r.stdout.strip()}


def d10_not_opted_in(env):
    env.skill("plain")
    env.config(projects=[], router=env.fake_router("routed", "plain"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_silent(r, p, "not opted in")
    expect(env.router_calls() == [], "router invoked without opt-in")
    (env.home / "hook.json").unlink()
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_silent(r, p, "no config")
    expect(env.router_calls() == [], "router invoked without config")


def d11_kill_switches(env):
    env.skill("plain")
    env.config(router=env.fake_router("routed", "plain"))
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"), env_extra={"TINK_HOOK": "off"})
    assert_silent(r, p, "TINK_HOOK=off")
    (env.proj / ".tink").mkdir(exist_ok=True)
    (env.proj / ".tink" / "hook.off").write_text("", encoding="utf-8")
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_silent(r, p, ".tink/hook.off")
    sub = env.proj / "sub" / "dir"
    sub.mkdir(parents=True)
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves", cwd=sub), cwd=sub)
    assert_silent(r, p, ".tink/hook.off from subdir")
    expect(env.router_calls() == [], "router invoked despite kill switch")


def d12_repo_cannot_enable(env):
    env.skill("plain")
    cfg = env.config(router=env.fake_router("routed", "plain"), write=False)
    for rel in (".tink/hook.json", "hook.json", ".tink-library/hook.json", ".tink/hook.on"):
        p = env.proj / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(cfg), encoding="utf-8")
    r, p, _ = env.hook(env.prompt("please write me a poem about autumn leaves"))
    assert_silent(r, p, "repo file enable")
    expect(env.router_calls() == [], "router invoked via repo-file config")


def d13_skip_rules(env):
    env.skill("plain")
    env.config(router=env.fake_router("routed", "plain"))
    for text in ("/compact now please do it", "short one", "", "   \n\t  ", "/tink-route whatever long enough"):
        r, p, _ = env.hook(env.prompt(text))
        assert_silent(r, p, f"skip {text!r}")
    expect(env.router_calls() == [], "router invoked for skipped prompt")


SECRETS = [
    "sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789",
    "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123",
    "AKIAIOSFODNN7EXAMPLE",
    "xoxb-123456789012-abcdefghijkl",
    "hunter2-supersecret",
    "MIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7",
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "eyJhbGciOiJIUzI1NiJ9QWxhZGRpbjpvcGVuIHNlc2FtZTEyMzQ1Njc4OTA",
]


def d14_secret_scrub(env):
    env.config(router=env.fake_router("noskill"))
    text = (
        "Help me debug my deploy script. My key is sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789 and "
        "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef0123, aws AKIAIOSFODNN7EXAMPLE, slack "
        "xoxb-123456789012-abcdefghijkl. Config has password=hunter2-supersecret and\n"
        "-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASCBKcwggSjAgEAAoIBAQC7\n-----END PRIVATE KEY-----\n"
        "sha 9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08 blob "
        "eyJhbGciOiJIUzI1NiJ9QWxhZGRpbjpvcGVuIHNlc2FtZTEyMzQ1Njc4OTA done."
    )
    r, p, _ = env.hook(env.prompt(text))
    assert_silent(r, p, "scrub")
    calls = env.router_calls()
    expect(len(calls) == 1, "router not invoked")
    sent = " ".join(calls[0]["argv"])
    for s in SECRETS:
        expect(s not in sent, f"secret leaked to router: {s[:12]}...")
    expect("debug my deploy script" in sent, "benign text removed")
    return {"sent": sent}


def d15_dedupe(env):
    env.skill("plain")
    env.config(router=env.fake_router("routed", "plain"))
    q = "please write me a poem about autumn leaves"
    r, p, _ = env.hook(env.prompt(q, session="sess-A"))
    assert_injected(env, r, p, "plain")
    r, p, _ = env.hook(env.prompt(q, session="sess-A"))
    assert_silent(r, p, "duplicate turn")
    r, p, _ = env.hook(env.prompt(q, session="sess-B"))
    assert_injected(env, r, p, "plain")
    r, p, _ = env.hook(env.prompt("", session="sess-A", event="SessionStart", source="startup"))
    assert_silent(r, p, "SessionStart startup")
    r, p, _ = env.hook(env.prompt(q, session="sess-A"))
    assert_silent(r, p, "after startup still deduped")
    r, p, _ = env.hook(env.prompt("", session="sess-A", event="SessionStart", source="compact"))
    assert_silent(r, p, "SessionStart compact")
    r, p, _ = env.hook(env.prompt(q, session="sess-A"))
    assert_injected(env, r, p, "plain")
    # approving a changed skill (new digest) re-injects in the same session
    (env.lib / "plain" / "SKILL.md").write_text(skill_md("plain", "Changed body.\n"), encoding="utf-8")
    env.run("tink", "library", "approve", "plain")
    r, p, _ = env.hook(env.prompt(q, session="sess-A"))
    assert_injected(env, r, p, "plain")
    # hostile session id stays inside .sessions
    evil = "../../../../escape"
    r, p, _ = env.hook(env.prompt(q, session=evil))
    assert_injected(env, r, p, "plain")
    sessions = env.proj / ".tink" / ".active" / ".sessions"
    expect(sessions.is_dir(), "sessions dir missing")
    expect(not (env.root / "escape").exists() and not (env.proj / "escape").exists(), "session id escaped")
    st = env.run("git", "status", "--porcelain", "--untracked-files=all").stdout
    expect(".tink/" not in st, f"session state not git-ignored: {st!r}")
    return {"session_files": sorted(p.name for p in sessions.iterdir())}


def d16_scripts_mount_ignored(env):
    env.skill("tooly", scripts={"run.sh": "echo hi\n"})
    env.config(router=env.fake_router("routed", "tooly"))
    r, p, _ = env.hook(env.prompt("please run the tooly workflow for this repo"))
    assert_injected(env, r, p, "tooly")
    link = env.proj / ".tink" / ".active" / "tooly"
    expect(link.is_symlink() or link.is_dir(), "scripts skill not mounted")
    st = env.run("git", "status", "--porcelain", "--untracked-files=all").stdout
    expect(".tink/.active" not in st, f".tink/.active not ignored: {st!r}")
    return {"git_status": st}


def d17_large_payload(env):
    unit = "Line with unicode é中\U0001f600 and tabs\t and quotes \"x\" <tag> & \\ backslash.\n"
    body = unit * (150000 // len(unit))
    env.skill("big", body=body)
    env.config(router=env.fake_router("routed", "big"))
    r, p, _ = env.hook(env.prompt("please do the big skill thing now"))
    _, content = assert_injected(env, r, p, "big")
    expect(len(content) > 140000, "payload shrank")
    return {"payload_chars": len(content)}


def d18_over_guard(env):
    env.skill("big", body="X" * 5000 + "\n")
    env.config(router=env.fake_router("routed", "big"))
    r, p, _ = env.hook(env.prompt("please do the big skill thing now"), env_extra={"TINK_HOOK_MAX_CHARS": "1000"})
    expect(r.returncode == 0, "exit != 0")
    expect(ctx_of(p) is None, "over-guard payload injected")
    expect("XXXXXXXXXX" not in r.stdout, "payload fragment leaked")
    msg = p.get("systemMessage", "") if isinstance(p, dict) else ""
    expect(msg.startswith("tink: skill big not applied") and "1000" in msg, f"notice {msg!r}")
    return {"notice": msg}


def d19_malformed_stdin(env):
    env.skill("plain")
    env.config(router=env.fake_router("routed", "plain"))
    variants = {
        "empty": "",
        "not_json": "this is not json",
        "array": "[1,2,3]",
        "null": "null",
        "empty_object": "{}",
        "missing_prompt": json.dumps({"session_id": "s", "cwd": str(env.proj), "hook_event_name": "UserPromptSubmit"}),
        "missing_cwd": json.dumps({"session_id": "s", "prompt": "please write a poem now", "hook_event_name": "UserPromptSubmit"}),
        "missing_session": json.dumps({"cwd": str(env.proj), "prompt": "please write a poem now", "hook_event_name": "UserPromptSubmit"}),
        "missing_event": json.dumps({"session_id": "s", "cwd": str(env.proj), "prompt": "please write a poem now"}),
        "prompt_not_string": json.dumps({"session_id": "s", "cwd": str(env.proj), "prompt": 42, "hook_event_name": "UserPromptSubmit"}),
        "cwd_missing_dir": json.dumps({"session_id": "s", "cwd": str(env.root / "nope"), "prompt": "please write a poem now", "hook_event_name": "UserPromptSubmit"}),
        "session_start_no_source": json.dumps({"session_id": "s", "cwd": str(env.proj), "hook_event_name": "SessionStart"}),
        "unknown_event": json.dumps({"session_id": "s", "cwd": str(env.proj), "hook_event_name": "Stop", "prompt": "please write a poem now"}),
        "binaryish": "\x00\x01\x02{\"a\":",
        "huge": "x" * 2_000_000,
    }
    results = {}
    for name, stdin in variants.items():
        r, p, _ = env.hook(stdin)
        results[name] = r.returncode
        expect(r.returncode == 0, f"{name}: exit {r.returncode} stderr={r.stderr[-300:]}")
        expect(ctx_of(p) is None, f"{name}: injected on malformed stdin")
    r, p, _ = env.hook("{}", args=())
    expect(r.returncode == 0, f"no-subcommand exit {r.returncode}")
    return results


def d20_enable_disable_status(env):
    sub = env.proj / "a" / "b"
    sub.mkdir(parents=True)
    r, _, _ = env.hook("", args=("status",), cwd=sub)
    expect(r.returncode == 0, "status failed")
    st = json.loads(r.stdout)
    expect(st["enabled"] is False and st["project"] == str(env.proj.resolve()), f"status {st}")
    r, _, _ = env.hook("", args=("enable",), cwd=sub)
    expect(r.returncode == 0, f"enable failed {r.stderr}")
    cfg = json.loads((env.home / "hook.json").read_text())
    expect(cfg["version"] == 1 and cfg["projects"] == [str(env.proj.resolve())], f"config {cfg}")
    expect(cfg.get("router_cmd") is None, "router_cmd set by enable")
    env.hook("", args=("enable",), cwd=sub)
    cfg = json.loads((env.home / "hook.json").read_text())
    expect(len(cfg["projects"]) == 1, "enable not idempotent")
    r, _, _ = env.hook("", args=("status",), cwd=sub)
    expect(json.loads(r.stdout)["enabled"] is True, "status not enabled")
    r, _, _ = env.hook("", args=("disable",), cwd=sub)
    cfg = json.loads((env.home / "hook.json").read_text())
    expect(cfg["projects"] == [], f"disable left {cfg}")
    leftovers = [p.name for p in env.home.iterdir() if p.name.startswith(".hook") or p.suffix == ".tmp"]
    expect(leftovers == [], f"temp files left: {leftovers}")


def d21_print_settings(env):
    r, _, _ = env.hook("", args=("print-settings",))
    expect(r.returncode == 0, "print-settings failed")
    s = json.loads(r.stdout)
    hooks = s["hooks"]
    ups = hooks["UserPromptSubmit"][0]
    expect("matcher" not in ups, "UserPromptSubmit must not have a matcher")
    h = ups["hooks"][0]
    expect(h == {"type": "command", "command": "tink-hook claude-code", "timeout": h.get("timeout")}, f"{h}")
    expect(isinstance(h["timeout"], int) and 3 <= h["timeout"] <= 30, f"timeout {h['timeout']}")
    ss = hooks["SessionStart"][0]
    expect(ss["matcher"] == "compact" and ss["hooks"][0]["command"] == "tink-hook claude-code", f"{ss}")
    expect(not (env.userhome / ".claude").exists(), "print-settings wrote ~/.claude")
    return s


def d22_entrypoint(env):
    data = tomllib.loads((REPO / "pyproject.toml").read_text())
    ep = data["project"]["scripts"].get("tink-hook")
    expect(ep == "tink_route.hook:main", f"entrypoint {ep!r}")
    expect(data["project"]["dependencies"] == [], "runtime dependency added")


DETERMINISTIC = [
    ("D1", "routed -> exact payload injected + notice", d1_routed),
    ("D2", "multi_routed -> top-1 only", d2_multi_top1),
    ("D3", "router no-skill -> silent", d3_no_skill),
    ("D4", "router error exit 2 -> silent", d4_router_error),
    ("D5", "router timeout -> silent within deadline, child killed", d5_timeout),
    ("D6", "router garbage -> silent", d6_garbage),
    ("D7", "router contract_version mismatch -> silent", d7_contract_mismatch),
    ("D8", "mount refusal (unapproved) -> no payload, notice only", d8_unapproved),
    ("D9", "symlinked SKILL.md -> refused, nothing leaked", d9_symlinked_skill_md),
    ("D10", "not opted in -> silent, router never invoked", d10_not_opted_in),
    ("D11", "kill switches: TINK_HOOK=off, .tink/hook.off", d11_kill_switches),
    ("D12", "repo files cannot enable or set router_cmd", d12_repo_cannot_enable),
    ("D13", "slash command / short / empty prompt skipped", d13_skip_rules),
    ("D14", "secret scrub before router", d14_secret_scrub),
    ("D15", "dedupe per session; new session; compact clears; digest change", d15_dedupe),
    ("D16", "scripts skill mounts; .tink/.active git-ignored", d16_scripts_mount_ignored),
    ("D17", "large payload round-trips byte-exact under guard", d17_large_payload),
    ("D18", "over-guard payload -> nothing injected + notice", d18_over_guard),
    ("D19", "malformed stdin variants -> exit 0, nothing injected", d19_malformed_stdin),
    ("D20", "enable/disable/status keyed by canonical git root", d20_enable_disable_status),
    ("D21", "print-settings snippet (never writes ~/.claude)", d21_print_settings),
    ("D22", "console script entrypoint, zero runtime deps", d22_entrypoint),
]


# ---------------------------------------------------------------- live cases

def live_env(env):
    for name in LIVE_SKILLS:
        src = REAL_LIBRARY / name
        if src.is_symlink() or not (src / "SKILL.md").is_file():
            continue
        shutil.copytree(src, env.lib / name, symlinks=True)
    r = env.run("tink", "library", "approve", "--all")
    expect(r.returncode == 0, f"approve --all failed: {r.stderr}")
    env.config(router=None)


def snapshot(*roots):
    snap = {}
    for root in roots:
        for p in sorted(root.rglob("*")):
            if ".git" in p.relative_to(root).parts:
                continue
            st = p.lstat()
            key = str(p)
            if p.is_symlink():
                snap[key] = ("link", os.readlink(p))
            elif p.is_file():
                snap[key] = ("file", st.st_size, st.st_mtime_ns, p.read_bytes())
            else:
                snap[key] = ("dir",)
    return snap


def l1_eli5(env):
    live_env(env)
    r, p, elapsed = env.hook(env.prompt("Explain like I'm five: how does a hash map work?"))
    expect(elapsed < 3.0, f"hook wall time {elapsed:.2f}s >= 3s")
    assert_injected(env, r, p, "eli5")
    return {"elapsed_s": round(elapsed, 3)}


def l2_unrelated(env):
    live_env(env)
    text = "Thanks, that looks good to me. Carry on."
    r, p, elapsed = env.hook(env.prompt(text))
    expect(elapsed < 3.0, f"hook wall time {elapsed:.2f}s >= 3s")
    assert_silent(r, p, "unrelated")
    rr = env.run(sys.executable, "-m", "tink_route.cli", "--json", "--deadline", "2", "--", text)
    out = json.loads(rr.stdout)
    expect(rr.returncode == 1 and out.get("contract_version") == 1, f"router exit {rr.returncode}: {rr.stdout[-300:]}")
    expect(out.get("action") == {"type": "none", "mount_command": None}, f"action {out.get('action')}")
    return {"elapsed_s": round(elapsed, 3), "router_status": out.get("status")}


def l3_router_contract_readonly(env):
    live_env(env)
    before = snapshot(env.proj, env.home)
    r = env.run(sys.executable, "-m", "tink_route.cli", "--json", "--",
                "Explain like I'm five: how does a hash map work?")
    after = snapshot(env.proj, env.home)
    expect(r.returncode == 0, f"router exit {r.returncode}: {r.stdout[-300:]}")
    out = json.loads(r.stdout)
    expect(out.get("contract_version") == 1, "contract_version missing")
    expect(out.get("status") == "routed" and out.get("winner") == "eli5", f"status {out.get('status')} {out.get('winner')}")
    act = out.get("action") or {}
    expect(act.get("type") in ("inject", "mount_and_inject"), f"action {act}")
    expect(act.get("mount_command") == "tink mount eli5 --json --payload", f"mount_command {act}")
    body = (env.lib / "eli5" / "SKILL.md").read_text(encoding="utf-8")
    probe = body.split("---", 2)[-1].strip()[:200]
    expect(probe not in r.stdout, "skill content present in router output")
    changed = sorted(set(before) ^ set(after) | {k for k in before if k in after and before[k] != after[k]})
    expect(changed == [], f"router wrote files: {changed[:5]}")
    return {"action": act, "files_snapshotted": len(before)}


LIVE = [
    ("L1", "live: ELI5 prompt routes + injects eli5 < 3s", l1_eli5),
    ("L2", "live: unrelated prompt injects nothing < 3s", l2_unrelated),
    ("L3", "live: router contract fields + read-only tree proof", l3_router_contract_readonly),
]


def main():
    only = None
    if "--only" in sys.argv:
        only = set(sys.argv[sys.argv.index("--only") + 1].split(","))
    if not TINK_BIN.is_file():
        print(f"tink binary not found: {TINK_BIN}", file=sys.stderr)
        return 1
    tink_version = subprocess.run([str(TINK_BIN), "--version"], capture_output=True, text=True).stdout.strip()
    have_key = bool(os.environ.get("TYPESAFE_API_KEY"))
    results = []
    for cid, title, fn in DETERMINISTIC + LIVE:
        if only and cid not in only:
            continue
        if cid.startswith("L") and not have_key:
            results.append({"id": cid, "title": title, "status": "SKIP", "detail": "TYPESAFE_API_KEY not set"})
            print(f"SKIP {cid} {title}")
            continue
        with tempfile.TemporaryDirectory(prefix=f"tink-hook-{cid}-") as tmp:
            t0 = time.monotonic()
            try:
                env = Env(Path(tmp).resolve())
                detail = fn(env)
                status = "PASS"
            except AssertionError as e:
                status, detail = "FAIL", str(e)
            except Exception as e:
                status, detail = "FAIL", f"{type(e).__name__}: {e}"
            ms = int((time.monotonic() - t0) * 1000)
        if isinstance(detail, dict) and "sent" in detail:
            detail = {"sent_redacted_len": len(detail["sent"]), "redactions": detail["sent"].count("[REDACTED]")}
        results.append({"id": cid, "title": title, "status": status, "ms": ms, "detail": detail})
        print(f"{status} {cid} {title}" + ("" if status == "PASS" else f" :: {detail}"))
    summary = {s: sum(1 for r in results if r["status"] == s) for s in ("PASS", "FAIL", "SKIP")}
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps({
        "suite": "tink-hook e2e",
        "tink_version": tink_version,
        "tink_bin": str(TINK_BIN),
        "python": sys.version.split()[0],
        "live": have_key,
        "summary": summary,
        "cases": results,
    }, indent=2, default=str) + "\n", encoding="utf-8")
    print(f"summary {summary} artifact {ARTIFACT}")
    return 1 if summary["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main())
