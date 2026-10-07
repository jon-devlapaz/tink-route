"""The tink-route flow: route a task, then deliver the winning skill or just report it.

Delivery (the default) verifies the winner with `tink mount --json --payload` and prints
it on stdout as an ordinary command result. It fails OPEN: any problem yields a one-line
message and a non-zero exit so the agent proceeds without a skill. Skill content is
printed only after tink has verified it. `--pick` stops after the routing decision.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .adapters.client import JevRouterClient
from .adapters.executor import SubprocessExecutor
from .core.constants import get_default_library_path, get_default_tink_home
from .core.credentials import KeyFileInsecure, key_file_path, resolve_api_key
from .core.models import RoutingResult
from .core.validation import is_valid_skill_name
from .metadata import load_library_skills, resolve_skillset

try:  # POSIX only; receipts degrade to unlocked O_APPEND elsewhere.
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]

CONTRACT_VERSION = 1
NO_SKILL_MESSAGE = "No specialist skill applies to this task; proceed without one."
PICK_NO_SKILL_MESSAGE = "No specialist skill applies to this task."
RouteFn = Callable[[str, list, Any], RoutingResult]


class FlowError(Exception):
    """A failure that must fail open. `reason` is a stable slug."""

    def __init__(self, reason: str, message: str = "", sentence: str | None = None):
        super().__init__(message or reason)
        self.reason = reason
        self.sentence = sentence  # a complete user-facing line, when the generic one would mislead


def _default_route(task: str, skills: list, args: Any) -> RoutingResult:
    try:
        api_key, _source = resolve_api_key()
    except KeyFileInsecure:
        raise FlowError("key_file_insecure", sentence=(
            f"Skill routing unavailable: the key file {key_file_path()} must be a regular file you own and can "
            "read, readable only by you (chmod 600 it); proceed without a skill.\n")) from None
    if not api_key:
        raise FlowError("no_api_key", sentence=(
            f"Skill routing unavailable: no TypeSafe API key was found (set TYPESAFE_API_KEY, or put the key in "
            f"{key_file_path()} with mode 600); proceed without a skill.\n"))
    if args.deadline is not None:
        if not (0 < args.deadline <= 600):
            raise FlowError("bad_deadline", "--deadline must be between 0 and 600 seconds")
        client = JevRouterClient(api_key=api_key, model=args.model, timeout=args.deadline, max_retries=0)
    else:
        client = JevRouterClient(api_key=api_key, model=args.model)
    return client.route(
        task,
        skills,
        threshold=args.threshold,
        tri_gate=args.tri_gate,
        rerank=args.rerank,
        fits_threshold=args.fits_threshold,
    )


def _write_receipt(path: Path, record: dict) -> None:
    """Append one JSON line, flock'd on the receipt's own descriptor (no sidecar file).

    Raises OSError/ValueError on any problem.
    """
    if path.is_symlink():
        raise OSError("refusing symlinked receipt path")
    path.parent.mkdir(parents=True, exist_ok=True)
    line = (json.dumps(record, ensure_ascii=False) + "\n").encode("utf-8")
    fd = os.open(
        path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0),
        0o644,
    )
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            if os.write(fd, line) != len(line):
                raise OSError("short write")
        finally:
            if fcntl is not None:
                fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


_SKILLSET_FIX = "see `tink skillset list` or check the pin under .tink/skillsets or $TINK_HOME/skillsets"
_ROUTING_FIXES = {
    "no_api_key": "no TypeSafe API key was found (set TYPESAFE_API_KEY, or use the key file; see README)",
    "key_file_insecure": "the routing key file must be a regular file you own, readable only by you (chmod 600 it)",
    "route_failed": "the routing service call failed (network or API error; retry later)",
    "library_missing": "the skill library was not found (default ~/.tink-library/skills or "
                       "$TINK_HOME/skills; override with --library)",
    "library_mismatch": "--library differs from Tink's delivery library (use --pick to inspect it, "
                        "or set TINK_HOME to its parent for both routing and delivery)",
    "library_unreadable": "the skill library could not be read (run `tink doctor`)",
    "skillset_error": "the skillset could not be resolved (" + _SKILLSET_FIX + ")",
}

_APPROVE = "review it, then run `tink library approve {name}`"
_DELIVERY_FIXES = {
    "unapproved": "it is not approved (" + _APPROVE + ")",
    "digest_mismatch": "it changed since approval (" + _APPROVE + ")",
    "symlink_refused": "a symlink was found in the skill (replace it with a real copy)",
    "identity_mismatch": "its frontmatter name differs from its directory name",
    "not_found": "it was not found in the skill library",
    "tink_not_found": "the `tink` CLI is not on PATH (install it or fix PATH)",
}


def failure_text(reason: str, skill: str | None) -> str:
    """Plain-language, user-facing failure line. The slug stays in receipts and --json."""
    if skill:
        why = _DELIVERY_FIXES.get(reason)
        if why is None and reason.startswith("invalid_") and reason != "invalid_winner":
            why = f"tink rejected it as invalid ({reason})"
        head = f"Skill '{skill}' was selected but could not be delivered"
        if why is None:
            return f"{head} ({reason}); proceed without it.\n"
        return f"{head}: {why.format(name=skill)}; proceed without it.\n"
    sentence = _ROUTING_FIXES.get(reason)
    if sentence is None:
        return f"Skill routing failed ({reason}); proceed without a skill.\n"
    return f"Skill routing unavailable: {sentence}; proceed without a skill.\n"


def _mount(executor: SubprocessExecutor, name: str, cwd: Path, payload: bool) -> dict:
    """Run `tink mount`; return parsed JSON (payload mode) or {} (plain). Raises FlowError."""
    cmd = ["tink", "mount", name] + (["--json", "--payload"] if payload else [])
    try:
        code, stdout, _stderr = executor.run(cmd, cwd=cwd)
    except FileNotFoundError:
        raise FlowError("tink_not_found") from None
    except OSError:
        raise FlowError("tink_unavailable") from None
    data: Any = None
    try:
        data = json.loads(stdout) if stdout.strip().startswith("{") else None
    except ValueError:
        data = None
    if code != 0:
        c = data.get("code") if isinstance(data, dict) else None
        raise FlowError(c if isinstance(c, str) and c.isidentifier() else "mount_failed")
    if not payload:
        return {}
    if not isinstance(data, dict):
        raise FlowError("bad_mount_output")
    pl = data.get("payload")
    digest = data.get("tree_digest")
    if (
        data.get("skill") != name
        or not isinstance(pl, dict)
        or not isinstance(pl.get("content"), str)
        or not isinstance(digest, str)
        or not digest.startswith("sha256:")
    ):
        raise FlowError("bad_mount_output")
    return data


def _pin_members(directory: Path) -> dict[str, set[str]]:
    """{pin name: members} for the readable `*.json` pins in a directory (bad pins are skipped)."""
    out: dict[str, set[str]] = {}
    for f in sorted(directory.glob("*.json")) if directory.is_dir() else []:
        try:
            members = json.loads(f.read_text(encoding="utf-8")).get("members")
        except Exception:
            continue
        if isinstance(members, list):
            out[f.stem] = {m for m in members if isinstance(m, str)}
    return out


def shelves_of(skill: str, cwd: Path) -> list[str]:
    """Pins whose members contain `skill`: project pins first, then home pins, sorted, at most 2."""
    project = _pin_members(cwd / ".tink" / "skillsets")
    home = {k: v for k, v in _pin_members(get_default_tink_home() / "skillsets").items() if k not in project}
    names = [n for pins in (project, home) for n in sorted(pins) if skill in pins[n]]
    return names[:2]


class Decision:
    """Where routing stands: the candidates offered, the shelf, the result, and any off-shelf hint."""

    def __init__(self, skillset: str | None):
        self.skillset = skillset
        self.scope = "skillset" if skillset else "library"
        self.skills: list = []
        self.result: RoutingResult | None = None
        self.skill: str | None = None  # the winner, once it has been validated
        self.hint: str | None = None  # a skill on another shelf that fits; never delivered
        self.hint_shelves: list[str] = []

    @property
    def routed(self) -> bool:
        return self.result is not None and self.result.status == "routed" and bool(self.result.winner)

    def hint_dict(self) -> dict | None:
        return {"skill": self.hint, "shelves": self.hint_shelves} if self.hint else None

    def no_skill_text(self, *, pick: bool = False) -> str:
        if pick:
            text = PICK_NO_SKILL_MESSAGE + "\n"
        elif self.scope == "skillset":
            text = f"No specialist skill on the {self.skillset} shelf applies to this task; proceed without one.\n"
        else:
            text = NO_SKILL_MESSAGE + "\n"
        if self.hint:
            where = (f"is on another shelf ({', '.join(self.hint_shelves)})" if self.hint_shelves
                     else "is not on any stage shelf")
            text += f"Hint: {self.hint} fits but {where}; it was not delivered.\n"
        return text


def decide(d: Decision, task: str, args: Any, cwd: Path, route_fn: RouteFn | None) -> None:
    """Choose a skill for `task`, filling in `d`. Raises FlowError on any failure.

    The shelf is `--skillset`, else (also with `--anywhere`) the whole library; AGENTS.md is never read.
    With a shelf, candidates are minus the skillset's `required` skills (tink already compiled those),
    and the shelf is strict: when it yields nothing, one more call over the rest of the library may
    produce a hint, never a delivery.
    """
    library = Path(args.library)
    if not library.is_dir():
        raise FlowError("library_missing")
    if not args.pick and library.resolve() != get_default_library_path().resolve():
        raise FlowError("library_mismatch")
    d.scope = "skillset" if d.skillset else "library"
    allowed: set[str] | None = None
    required: set[str] = set()
    if d.skillset:
        try:
            allowed, required = resolve_skillset(d.skillset, tink_home=get_default_tink_home(), project_dir=cwd)
        except Exception:
            raise FlowError("skillset_error") from None
    try:
        everything = [s for s in load_library_skills(library) if s["name"] not in required]
    except Exception:
        raise FlowError("library_unreadable") from None

    def attempt(candidates: list) -> None:
        d.skills = candidates
        if not candidates:
            d.result = RoutingResult(status="no_candidates_available", task=task)
            return
        try:
            d.result = (route_fn or _default_route)(task, candidates, args)
        except FlowError:
            raise
        except Exception:
            raise FlowError("route_failed") from None

    shelf = everything if allowed is None else [s for s in everything if s["name"] in allowed]
    attempt(shelf)

    if d.routed:
        assert d.result is not None and d.result.winner is not None
        d.skill = d.result.winner
        if not is_valid_skill_name(d.skill) or d.skill not in {s["name"] for s in d.skills}:
            raise FlowError("invalid_winner")
    elif allowed is not None:
        on_shelf = {s["name"] for s in shelf}
        rest = [s for s in everything if s["name"] not in on_shelf]
        if rest:
            try:
                res = (route_fn or _default_route)(task, rest, args)
            except Exception:
                return  # the hint is a courtesy; the primary answer stands
            name = res.winner if res.status == "routed" else None
            if name and is_valid_skill_name(name) and name in {s["name"] for s in rest}:
                d.hint = name
                d.hint_shelves = shelves_of(name, cwd)


def pick_json(d: Decision, task: str) -> dict:
    """The routing decision as JSON. Never includes skill content."""
    assert d.result is not None
    out = d.result.to_dict()
    res = {"contract_version": CONTRACT_VERSION, "status": out.pop("status"), "task": out.pop("task"),
           "skillset": d.skillset, "scope": d.scope, **out}
    if not d.routed:
        res["hint"] = d.hint_dict()
    return res


def pick(args: Any, *, route_fn: RouteFn | None = None) -> int:
    """`--pick`: decide only. Mounts nothing, writes nothing. Exit 0 routed, 1 no skill, 2 error."""
    task = args.task.strip()
    d = Decision(args.skillset)
    try:
        decide(d, task, args, Path.cwd(), route_fn)
    except FlowError as e:
        if args.json:
            print(json.dumps({"contract_version": CONTRACT_VERSION, "status": "error", "reason": e.reason}, indent=2))
        else:
            sys.stderr.write(e.sentence or failure_text(e.reason, d.skill))
        return 2
    if args.json:
        print(json.dumps(pick_json(d, task), indent=2))
    elif d.routed:
        conf = d.result.confidence if d.result.confidence is not None else 0.0  # type: ignore[union-attr]
        print(f"Skill: {d.skill} (confidence {conf:.2f})")
    else:
        sys.stdout.write(d.no_skill_text(pick=True))
    return 0 if d.routed else 1


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
            "hint": rec.get("hint"),
        }, indent=2))
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def deliver(args: Any, *, route_fn: RouteFn | None = None, executor: SubprocessExecutor) -> int:
    """Default mode: route, verify with tink, print the skill. Exit 0 delivered, 1 no skill, 2 failed."""
    task = args.task.strip()
    d = Decision(args.skillset)
    rec: dict[str, Any] = {
        "task": args.task, "skillset": d.skillset, "status": "error", "skill": None,
        "tree_digest": None, "chars": None, "delivery": "none", "confidence": None,
        "reason": None, "path": None, "scope": d.scope, "hint": None,
    }
    content: str | None = None
    text = ""
    code = 2
    cwd = Path.cwd()

    try:
        try:
            decide(d, task, args, cwd, route_fn)
        finally:
            rec["scope"] = d.scope
            rec["skillset"] = d.skillset
            rec["skill"] = d.skill
            if d.result is not None:
                rec["confidence"] = d.result.confidence
                rec["reason"] = d.result.status
        if not d.routed:
            rec["status"] = "no_skill"
            rec["hint"] = d.hint_dict()
            text = d.no_skill_text()
            code = 1
        else:
            assert d.result is not None and d.skill is not None
            name = d.skill
            data = _mount(executor, name, cwd, payload=True)
            payload = data["payload"]["content"]
            chars = data["payload"].get("chars")
            chars = chars if isinstance(chars, int) and not isinstance(chars, bool) else len(payload)
            rec.update(tree_digest=data["tree_digest"], chars=chars)
            conf = d.result.confidence if d.result.confidence is not None else 0.0
            header = (f"# tink skill: {name}  (digest {data['tree_digest']}, {chars} chars, "
                      f"confidence {conf:.2f})\n")
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
    except FlowError as e:
        rec.update(status="error", reason=e.reason, delivery="none", path=None)
        rec.update(tree_digest=None, chars=None)
        content = None
        code = 2
        text = e.sentence or failure_text(e.reason, rec["skill"])

    _emit(args, rec, text, content)

    receipt = args.receipt or os.environ.get("TINK_ROUTE_RECEIPT")
    if receipt:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
            "task": rec["task"], "skillset": rec["skillset"], "status": rec["status"],
            "skill": rec["skill"], "tree_digest": rec["tree_digest"], "chars": rec["chars"],
            "delivery": rec["delivery"], "confidence": rec["confidence"], "reason": rec["reason"],
            "scope": rec["scope"], "hint_skill": (rec["hint"] or {}).get("skill"),
        }
        try:
            _write_receipt(Path(receipt), record)
        except (OSError, ValueError) as e:
            print(f"tink-route: warning: could not write receipt ({e.__class__.__name__}: {e})",
                  file=sys.stderr)
    return code
