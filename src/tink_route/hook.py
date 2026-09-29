"""tink-hook: harness-neutral route core plus a Claude Code adapter for ambient skill activation (route -> mount -> inject).

The agent never sees a skill catalog. On each prompt this hook asks the router
whether one skill applies, has `tink mount --json --payload` verify and read it,
and returns the whole skill as additionalContext, or nothing at all.

`tink-hook claude-code` must never block a prompt: every path exits 0, and every
error, timeout, refusal or opt-out prints nothing (or only a one-line notice).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

from .core.constants import get_default_tink_home
from .core.validation import is_valid_skill_name

CONTRACT_VERSION = 1
CONFIG_VERSION = 1
CONFIG_NAME = "hook.json"
DEFAULT_DEADLINE = 2.0
DEFAULT_MOUNT_DEADLINE = 1.5
DEFAULT_MAX_CHARS = 200_000
MIN_PROMPT_CHARS = 12
SETTINGS_TIMEOUT_SECONDS = 10
OFF_VALUES = {"off", "0", "false", "no", "disable", "disabled"}
HOOK_COMMAND = "tink-hook claude-code"
DISABLE_HINT = "disable: TINK_HOOK=off"
FRAMING = (
    "tink: the skill below was selected for this request. Apply its instructions to the user's "
    "request; do not mention this mechanism unless the user asks."
)
DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
CODE_RE = re.compile(r"[a-z0-9_-]{1,40}")
SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")

REDACTED = "[REDACTED]"
_SECRET_PATTERNS = [
    re.compile(r"-----BEGIN [A-Z0-9 ]*-----.*?(?:-----END [A-Z0-9 ]*-----|\Z)", re.DOTALL),
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"),
    re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    re.compile(r"\bxox[abposr]-[A-Za-z0-9-]{10,}"),
    re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{8,}", re.IGNORECASE),
    re.compile(r"\b[0-9a-fA-F]{32,}\b"),
    re.compile(r"(?=[A-Za-z0-9+/_=-]*[0-9])(?=[A-Za-z0-9+/_=-]*[A-Z])(?=[A-Za-z0-9+/_=-]*[a-z])[A-Za-z0-9+/_-]{40,}={0,2}"),
]
_ASSIGNMENT = re.compile(
    r"(?i)\b((?:[a-z0-9_]*_)?(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|"
    r"client[_-]?secret|auth|credentials?))(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|\S+)"
)


def scrub_secrets(text: str) -> str:
    """Best-effort heuristic redaction before the prompt leaves the machine. Not a guarantee."""
    text = _ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{REDACTED}", text)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


def _env_float(name: str, default: float, lo: float, hi: float) -> float:
    try:
        value = float(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if lo <= value <= hi else default


def _env_int(name: str, default: int, lo: int, hi: int) -> int:
    try:
        value = int(os.environ.get(name, ""))
    except ValueError:
        return default
    return value if lo <= value <= hi else default


def config_path() -> Path:
    return get_default_tink_home() / CONFIG_NAME


def canonical_project(cwd: Path) -> Path:
    """Git top-level of cwd if inside a work tree, else cwd; always fully resolved."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            cwd=cwd, capture_output=True, text=True, timeout=1.0, check=False,
            stdin=subprocess.DEVNULL,
        )
        top = r.stdout.strip()
        if r.returncode == 0 and top:
            return Path(top).resolve()
    except (OSError, subprocess.SubprocessError):
        pass
    return cwd.resolve()


def load_config() -> dict[str, Any] | None:
    try:
        data = json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("version") != CONFIG_VERSION:
        return None
    projects = data.get("projects")
    if not isinstance(projects, list) or not all(isinstance(p, str) for p in projects):
        return None
    router = data.get("router_cmd")
    if router is not None and not (
        isinstance(router, list) and router and all(isinstance(a, str) and a for a in router)
    ):
        return None
    return {"version": CONFIG_VERSION, "projects": list(projects), "router_cmd": router}


def kill_switch(project: Path) -> dict[str, bool]:
    return {
        "env": os.environ.get("TINK_HOOK", "").strip().lower() in OFF_VALUES,
        "repo_file": os.path.lexists(project / ".tink" / "hook.off"),
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


TIMED_OUT = "timed_out"


def run_bounded(argv: list[str], cwd: Path, deadline: float) -> tuple[int, str] | str | None:
    """Run argv in its own process group; kill the whole group at the deadline.

    Returns (returncode, stdout), TIMED_OUT if the deadline killed it, or None if it did not start.
    """
    env = dict(os.environ)
    env.pop("TINK_ROUTE_INSTALL", None)
    try:
        proc = subprocess.Popen(
            argv, cwd=cwd, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
    except OSError:
        return None
    try:
        out, _ = proc.communicate(timeout=deadline)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            proc.kill()
        try:
            proc.communicate(timeout=1.0)
        except subprocess.SubprocessError:
            pass
        return TIMED_OUT
    return proc.returncode, out.decode("utf-8", errors="replace")


# ---------------------------------------------------------------- session dedupe

def _sessions_dir(project: Path) -> Path:
    return project / ".tink" / ".active" / ".sessions"


def _session_file(project: Path, session_id: str) -> Path:
    if SESSION_RE.fullmatch(session_id):
        name = session_id
    else:
        name = "h-" + hashlib.sha256(session_id.encode("utf-8", errors="replace")).hexdigest()[:40]
    return _sessions_dir(project) / name


def _load_seen(path: Path) -> set[str]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        seen = data.get("seen") if isinstance(data, dict) else None
        return {s for s in seen if isinstance(s, str)} if isinstance(seen, list) else set()
    except (OSError, ValueError):
        return set()


def _save_seen(path: Path, seen: set[str]) -> None:
    ignore = path.parent / ".gitignore"
    if not ignore.exists():
        _atomic_write(ignore, "*\n")
    _atomic_write(path, json.dumps({"version": 1, "seen": sorted(seen)}) + "\n")


def _clear_session(project: Path, session_id: str) -> None:
    try:
        _session_file(project, session_id).unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- claude-code flow

def _route(task: str, project: Path, cfg: dict[str, Any], deadline: float) -> tuple[str | None, str | None]:
    """Return (winner, None) or (None, reason slug)."""
    if cfg["router_cmd"]:
        argv = [*cfg["router_cmd"], task]
    else:
        argv = [sys.executable, "-m", "tink_route.cli", "--json", "--deadline", f"{deadline:g}", "--", task]
    ran = run_bounded(argv, project, deadline)
    if ran is None or ran == TIMED_OUT:
        return None, "router_timeout" if ran == TIMED_OUT else "router_error"
    try:
        out = json.loads(ran[1])
    except ValueError:
        return None, "router_error"
    if not isinstance(out, dict) or out.get("contract_version") != CONTRACT_VERSION:
        return None, "router_error"
    status = out.get("status")
    if status == "no_skill_needed":
        return None, "no_skill"
    if ran[0] != 0:
        return None, "router_error"
    if status not in ("routed", "multi_routed"):
        return None, "no_skill"
    winner = out.get("winner")
    if isinstance(winner, str) and is_valid_skill_name(winner):
        return winner, None
    return None, "router_error"


def _mount(skill: str, project: Path, deadline: float) -> tuple[dict[str, Any] | None, str | None]:
    """Return (mount facts, None) on success or (None, refusal code | None)."""
    tink = shutil.which("tink")
    if not tink:
        return None, None
    ran = run_bounded([tink, "mount", skill, "--json", "--payload"], project, deadline)
    if ran is None or ran == TIMED_OUT:
        return None, None
    try:
        out = json.loads(ran[1])
    except ValueError:
        return None, None
    if not isinstance(out, dict) or out.get("contract_version") != CONTRACT_VERSION:
        return None, None
    if ran[0] != 0:
        code = out.get("code")
        return None, code if isinstance(code, str) and CODE_RE.fullmatch(code) else "error"
    payload = out.get("payload")
    if out.get("skill") != skill or not isinstance(payload, dict):
        return None, None
    content = payload.get("content")
    digest = out.get("tree_digest")
    if not isinstance(content, str) or payload.get("truncated"):
        return None, None
    if payload.get("chars") != len(content) or not isinstance(digest, str) or not DIGEST_RE.fullmatch(digest):
        return None, None
    return {"content": content, "digest": digest}, None


def _none(reason: str, *, skill: str | None = None, notice: str | None = None) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION, "action": "none", "skill": skill, "tree_digest": None,
        "content": None, "notice": notice, "reason": reason,
    }


def _parse_route_request(raw: str) -> tuple[str, Path, int | None] | None:
    try:
        req = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(req, dict):
        return None
    prompt, session_id, cwd = req.get("prompt"), req.get("session_id"), req.get("cwd")
    if not isinstance(prompt, str) or not isinstance(session_id, str) or not session_id:
        return None
    if not isinstance(cwd, str) or not cwd:
        return None
    max_chars = req.get("max_chars")
    if max_chars is not None and (isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars < 1):
        return None
    cwd_path = Path(cwd)
    if not cwd_path.is_absolute() or not cwd_path.is_dir():
        return None
    return prompt, cwd_path, max_chars


def route(request: dict[str, Any]) -> dict[str, Any]:
    """Harness-neutral core: opt-in, kill switch, skip rules, scrub, router, mount, size guard.

    `request` is {"prompt", "session_id", "cwd", "max_chars"?}. Never raises: any error is action none.
    No dedupe and no harness framing; `content` is the raw mounted payload.
    """
    try:
        return _route_core(request)
    except Exception:
        return _none("internal")


def _route_core(request: dict[str, Any]) -> dict[str, Any]:
    parsed = _parse_route_request(json.dumps(request))
    if parsed is None:
        return _none("bad_input")
    prompt, cwd_path, max_chars = parsed
    project = canonical_project(cwd_path)
    if any(kill_switch(project).values()):
        return _none("not_enabled")
    cfg = load_config()
    if cfg is None or str(project) not in cfg["projects"]:
        return _none("not_enabled")
    text = prompt.strip()
    if not text or text.startswith("/") or len(text) < MIN_PROMPT_CHARS:
        return _none("skipped")

    deadline = _env_float("TINK_HOOK_DEADLINE", DEFAULT_DEADLINE, 0.1, 30.0)
    skill, reason = _route(scrub_secrets(text), project, cfg, deadline)
    if skill is None:
        return _none(reason or "router_error")

    mount_deadline = _env_float("TINK_HOOK_MOUNT_DEADLINE", DEFAULT_MOUNT_DEADLINE, 0.1, 30.0)
    facts, code = _mount(skill, project, mount_deadline)
    if facts is None:
        if code is None:
            return _none("internal", skill=skill)
        return _none(f"mount_refused:{code}", skill=skill, notice=f"tink: skill {skill} not applied ({code})")
    if max_chars is not None and len(facts["content"]) > max_chars:
        return _none("oversize", skill=skill, notice=f"tink: skill {skill} not applied (oversize)")
    return {
        "contract_version": CONTRACT_VERSION, "action": "inject", "skill": skill,
        "tree_digest": facts["digest"], "content": facts["content"],
        "notice": f"tink: applied skill {skill} ({DISABLE_HINT})", "reason": None,
    }


def cmd_route() -> int:
    out = _none("internal")
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        parsed = _parse_route_request(raw)
        if parsed is None:
            out = _none("bad_input")
        else:
            req = json.loads(raw)
            out = route(req)
    except BaseException:
        pass
    try:
        sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    except BaseException:
        pass
    return 0


# ---------------------------------------------------------------- claude-code adapter

def _notice(message: str) -> dict[str, Any]:
    return {"systemMessage": message}


def handle_claude_code(raw: str) -> dict[str, Any] | None:
    try:
        event = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    name = event.get("hook_event_name")
    session_id = event.get("session_id")
    cwd = event.get("cwd")
    if not isinstance(session_id, str) or not session_id or not isinstance(cwd, str) or not cwd:
        return None
    cwd_path = Path(cwd)
    if not cwd_path.is_absolute() or not cwd_path.is_dir():
        return None
    project = canonical_project(cwd_path)

    if name == "SessionStart":
        if event.get("source") in ("compact", "clear"):
            _clear_session(project, session_id)
        return None
    if name != "UserPromptSubmit":
        return None
    prompt = event.get("prompt")
    if not isinstance(prompt, str):
        return None

    result = route({"prompt": prompt, "session_id": session_id, "cwd": cwd})
    state = _session_file(project, session_id)
    reason = result["reason"] or ""
    if result["action"] == "none":
        if not reason.startswith("mount_refused:"):
            return None
        seen = _load_seen(state)
        key = f"refused:{result['skill']}:{reason.split(':', 1)[1]}"
        if key in seen:
            return None
        _remember(state, seen, key)
        return _notice(result["notice"])

    skill, digest = result["skill"], result["tree_digest"]
    seen = _load_seen(state)
    key = f"injected:{skill}:{digest}"
    if key in seen:
        return None
    context = (
        f"{FRAMING}\n"
        f'<tink-skill name="{skill}" digest="{digest}">\n'
        f"{result['content']}\n"
        "</tink-skill>"
    )
    max_chars = _env_int("TINK_HOOK_MAX_CHARS", DEFAULT_MAX_CHARS, 1, 100_000_000)
    if len(context) > max_chars:
        return _notice(
            f"tink: skill {skill} not applied (payload {len(context)} chars exceeds TINK_HOOK_MAX_CHARS={max_chars})"
        )
    _remember(state, seen, key)
    return {
        "hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context},
        "systemMessage": result["notice"],
    }


def _remember(state: Path, seen: set[str], key: str) -> None:
    try:
        _save_seen(state, seen | {key})
    except OSError:
        pass


def cmd_claude_code() -> int:
    try:
        raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
        out = handle_claude_code(raw)
        if out is not None:
            sys.stdout.write(json.dumps(out) + "\n")
            sys.stdout.flush()
    except BaseException:
        pass
    return 0


# ---------------------------------------------------------------- management commands

def _write_config(projects: list[str], router_cmd: Any) -> None:
    data = {"version": CONFIG_VERSION, "projects": projects, "router_cmd": router_cmd}
    _atomic_write(config_path(), json.dumps(data, indent=2) + "\n")


def _current_config_for_update() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {"version": CONFIG_VERSION, "projects": [], "router_cmd": None}
    cfg = load_config()
    if cfg is None:
        raise ValueError(f"refusing to overwrite unreadable or invalid config: {path}")
    return cfg


def cmd_enable(enable: bool) -> int:
    project = canonical_project(Path.cwd())
    try:
        cfg = _current_config_for_update()
    except ValueError as e:
        print(f"tink-hook: {e}", file=sys.stderr)
        return 2
    projects = [p for p in cfg["projects"] if p != str(project)]
    if enable:
        projects.append(str(project))
    _write_config(projects, cfg["router_cmd"])
    state = "enabled" if enable else "disabled"
    print(f"tink-hook {state} for {project} (config: {config_path()})")
    return 0


def cmd_status() -> int:
    project = canonical_project(Path.cwd())
    cfg = load_config()
    status = {
        "project": str(project),
        "enabled": bool(cfg and str(project) in cfg["projects"]),
        "config": str(config_path()),
        "config_valid": cfg is not None,
        "kill_switch": kill_switch(project),
        "tink": shutil.which("tink"),
    }
    print(json.dumps(status, indent=2))
    return 0


def settings_snippet() -> dict[str, Any]:
    hook = {"type": "command", "command": HOOK_COMMAND, "timeout": SETTINGS_TIMEOUT_SECONDS}
    return {
        "hooks": {
            "UserPromptSubmit": [{"hooks": [dict(hook)]}],
            "SessionStart": [{"matcher": "compact", "hooks": [dict(hook)]}],
        }
    }


USAGE = (
    "usage: tink-hook {claude-code|route|enable|disable|status|print-settings}\n"
    "  claude-code     Claude Code UserPromptSubmit/SessionStart hook (reads hook JSON on stdin)\n"
    "  route           harness-neutral core: one JSON request on stdin, one JSON decision on stdout\n"
    "  enable|disable  opt the current project in/out (user-scope $TINK_HOME/hook.json)\n"
    "  status          show opt-in and kill-switch state for the current project\n"
    "  print-settings  print the ~/.claude/settings.json hooks snippet (never writes it)\n"
)


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    cmd = args[0] if args else None
    if cmd == "claude-code":
        return cmd_claude_code()
    if cmd == "route":
        return cmd_route()
    if cmd in ("enable", "disable"):
        return cmd_enable(cmd == "enable")
    if cmd == "status":
        return cmd_status()
    if cmd == "print-settings":
        print(json.dumps(settings_snippet(), indent=2))
        return 0
    if cmd in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0
    sys.stderr.write(USAGE)
    return 0


if __name__ == "__main__":
    sys.exit(main())
