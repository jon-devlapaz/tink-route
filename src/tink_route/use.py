"""`tink-route --use`: agent-initiated skill delivery.

Route the task, let `tink mount --json --payload` verify the winner, and print the
skill on stdout as an ordinary command result. Capability routing fails OPEN: any
problem yields a one-line message and a non-zero exit so the agent proceeds without
a skill. Skill content is printed only after tink has verified it.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .adapters.client import JevRouterClient
from .adapters.executor import SubprocessExecutor
from .adapters.ledger import default_ledger
from .core.constants import STAGE_TO_SKILLSET, get_default_tink_home
from .core.engine import RoutingEngine
from .core.models import RoutingResult
from .core.validation import is_valid_skill_name
from .metadata import load_library_skills, resolve_skillset

try:  # POSIX only; receipts degrade to unlocked O_APPEND elsewhere.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

CONTRACT_VERSION = 1
NO_SKILL_MESSAGE = "No specialist skill applies to this task; proceed without one."
RouteFn = Callable[[str, list, Any], RoutingResult]


class UseError(Exception):
    """A failure that must fail open. `reason` is a stable slug."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(message or reason)
        self.reason = reason


def _default_route(task: str, skills: list, args: Any) -> RoutingResult:
    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        raise UseError("no_api_key", "TYPESAFE_API_KEY is not set")
    if args.deadline is not None:
        if not (0 < args.deadline <= 600):
            raise UseError("bad_deadline", "--deadline must be between 0 and 600 seconds")
        client = JevRouterClient(api_key=api_key, model=args.model, timeout=args.deadline, max_retries=0)
    else:
        client = JevRouterClient(api_key=api_key, model=args.model)
    engine = RoutingEngine(client=client, ledger=default_ledger)
    return engine.route(
        task=task,
        skills=skills,
        threshold=args.threshold,
        install=False,
        tri_gate=args.tri_gate,
        rerank=args.rerank,
        fits_threshold=args.fits_threshold,
        multi=False,
    )


def _write_receipt(path: Path, record: dict) -> None:
    """Append one JSON line under flock. Raises OSError/ValueError on any problem."""
    if path.is_symlink():
        raise OSError("refusing symlinked receipt path")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    lock_fd = None
    try:
        if fcntl is not None:
            lock_path = str(path) + ".lock"
            if os.path.islink(lock_path):
                raise OSError("refusing symlinked receipt lock path")
            lock_fd = os.open(lock_path, os.O_WRONLY | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            fcntl.flock(lock_fd, fcntl.LOCK_EX)
        fd = os.open(
            path,
            os.O_WRONLY | os.O_APPEND | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        try:
            if os.write(fd, line) != len(line):
                raise OSError("short write")
        finally:
            os.close(fd)
    finally:
        if lock_fd is not None:
            os.close(lock_fd)  # closing releases the flock


def _mount(executor: SubprocessExecutor, name: str, cwd: Path, payload: bool) -> dict:
    """Run `tink mount`; return parsed JSON (payload mode) or {} (plain). Raises UseError."""
    cmd = ["tink", "mount", name] + (["--json", "--payload"] if payload else [])
    try:
        code, stdout, _stderr = executor.run(cmd, cwd=cwd)
    except FileNotFoundError:
        raise UseError("tink_not_found") from None
    except OSError:
        raise UseError("tink_unavailable") from None
    data: Any = None
    try:
        data = json.loads(stdout) if stdout.strip().startswith("{") else None
    except ValueError:
        data = None
    if code != 0:
        c = data.get("code") if isinstance(data, dict) else None
        raise UseError(c if isinstance(c, str) and c.isidentifier() else "mount_failed")
    if not payload:
        return {}
    if not isinstance(data, dict):
        raise UseError("bad_mount_output")
    pl = data.get("payload")
    digest = data.get("tree_digest")
    if (
        data.get("skill") != name
        or not isinstance(pl, dict)
        or not isinstance(pl.get("content"), str)
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
    ):
        raise UseError("bad_mount_output")
    return data


def _emit(args: Any, rec: dict, text: str, content: str | None) -> None:
    if args.json:
        print(json.dumps({
            "contract_version": CONTRACT_VERSION,
            "status": rec["status"],
            "skill": rec["skill"],
            "tree_digest": rec["tree_digest"],
            "chars": rec["chars"],
            "delivery": rec["delivery"],
            "path": rec.get("path"),
            "confidence": rec["confidence"],
            "content": content if rec["delivery"] == "inline" else None,
            "reason": rec["reason"],
            "scope": rec.get("scope"),
        }, indent=2))
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def run_use(args: Any, *, route_fn: RouteFn | None = None, executor: SubprocessExecutor | None = None) -> int:
    # Usage errors: one stderr line, no receipt, no routing.
    for flag, on in (("--install", args.install), ("--prune", args.prune or args.task == "prune"),
                     ("--multi", args.multi)):
        if on:
            print(f"tink-route: error: --use cannot be combined with {flag}.", file=sys.stderr)
            return 2
    task = (args.task or "").strip()
    if not task:
        print('tink-route: error: --use requires a task: tink-route --use "<what you need>".', file=sys.stderr)
        return 2
    if getattr(args, "stage_only", False) and not (args.skillset or args.stage):
        print("tink-route: error: --stage-only needs --stage or --skillset.", file=sys.stderr)
        return 2
    if args.inline_max < 0:
        print("tink-route: error: --inline-max must be >= 0.", file=sys.stderr)
        return 2
    if executor is None:
        from .adapters.executor import DefaultSubprocessExecutor

        executor = DefaultSubprocessExecutor()

    skillset = args.skillset or (STAGE_TO_SKILLSET.get(args.stage) if args.stage else None)
    rec: dict[str, Any] = {
        "task": args.task, "skillset": skillset, "status": "error", "skill": None,
        "tree_digest": None, "chars": None, "delivery": "none", "confidence": None,
        "reason": None, "path": None, "scope": "skillset" if skillset else "library",
    }
    content: str | None = None
    text = ""
    code = 2
    cwd = Path.cwd()

    try:
        library = Path(args.library)
        if not library.is_dir():
            raise UseError("library_missing")
        required: set[str] = set()
        allowed: set[str] | None = None
        if skillset:
            try:
                allowed, required = resolve_skillset(
                    skillset, tink_home=get_default_tink_home(), project_dir=cwd)
            except Exception:
                raise UseError("skillset_error") from None
        try:
            skills = load_library_skills(library)
        except Exception:
            raise UseError("library_unreadable") from None
        if allowed is not None:
            skills = [s for s in skills if s["name"] in allowed]
        skills = [s for s in skills if s["name"] not in required]

        def attempt(candidates: list) -> RoutingResult | None:
            if not candidates:
                rec["reason"] = "no_candidates"
                return None
            try:
                res = (route_fn or _default_route)(task, candidates, args)
            except UseError:
                raise
            except Exception:
                raise UseError("route_failed") from None
            rec["confidence"] = res.confidence
            rec["reason"] = res.status
            return res

        result = attempt(skills)
        if ((result is None or result.status != "routed" or not result.winner)
                and allowed is not None and not getattr(args, "stage_only", False)):
            # A stage skillset is a hint, not a wall: retry once over the whole library
            # (the stage's required disciplines are still excluded).
            skills = [s for s in load_library_skills(library) if s["name"] not in required]
            rec["scope"] = "library"
            result = attempt(skills)

        if result is None or result.status != "routed" or not result.winner:
            rec["status"] = "no_skill"
            text = NO_SKILL_MESSAGE + "\n"
            code = 1
        else:
            name = result.winner
            rec["skill"] = name
            if not is_valid_skill_name(name) or name not in {s["name"] for s in skills}:
                raise UseError("invalid_winner")
            data = _mount(executor, name, cwd, payload=True)
            payload = data["payload"]["content"]
            chars = data["payload"].get("chars")
            chars = chars if isinstance(chars, int) and not isinstance(chars, bool) else len(payload)
            rec.update(tree_digest=data["tree_digest"], chars=chars)
            conf = result.confidence if result.confidence is not None else 0.0
            outside = f", outside the {skillset} skillset" if skillset and rec["scope"] == "library" else ""
            header = (f"# tink skill: {name}  (digest {data['tree_digest']}, {chars} chars, "
                      f"confidence {conf:.2f}{outside})\n")
            base = f".tink/.active/{name}"
            if chars <= args.inline_max:
                extra = ""
                if data.get("has_scripts"):
                    extra = f"Bundled scripts are available under {base}/scripts/.\n"
                text = header + "Apply these instructions to the current task.\n" + extra + "\n" + payload
                content = payload
                rec.update(delivery="inline")
            else:
                _mount(executor, name, cwd, payload=False)
                lines = [header.rstrip("\n"),
                         f"This skill is {chars} chars; read it in full before continuing: {base}/SKILL.md"]
                if data.get("has_scripts"):
                    lines.append(f"Bundled scripts: {base}/scripts/")
                if data.get("has_references"):
                    lines.append(f"References: {base}/references/")
                text = "\n".join(lines) + "\n"
                rec.update(delivery="path", path=f"{base}/SKILL.md")
            rec["status"] = "delivered"
            rec["reason"] = None
            code = 0
    except UseError as e:
        rec.update(status="error", reason=e.reason, delivery="none", path=None)
        rec.update(tree_digest=None, chars=None)
        content = None
        code = 2
        if rec["skill"]:
            text = (f"Skill '{rec['skill']}' was selected but could not be delivered "
                    f"({e.reason}); proceed without it.\n")
        else:
            text = f"Skill routing failed ({e.reason}); proceed without a skill.\n"

    _emit(args, rec, text, content)

    receipt = args.receipt or os.environ.get("TINK_ROUTE_RECEIPT")
    if receipt:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "task": rec["task"], "skillset": rec["skillset"], "status": rec["status"],
            "skill": rec["skill"], "tree_digest": rec["tree_digest"], "chars": rec["chars"],
            "delivery": rec["delivery"], "confidence": rec["confidence"], "reason": rec["reason"],
            "scope": rec["scope"],
        }
        try:
            _write_receipt(Path(receipt), record)
        except (OSError, ValueError) as e:
            print(f"tink-route: warning: could not write receipt ({e.__class__.__name__}: {e})",
                  file=sys.stderr)
    return code
