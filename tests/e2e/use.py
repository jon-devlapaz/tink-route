#!/usr/bin/env python3
"""E2E for `tink-route` (delivery and `--pick`), driving the real CLI as a subprocess.

Offline cases need no router. Live cases (real Jev router, <= 12 API calls) are
skipped cleanly without TYPESAFE_API_KEY. Everything runs in an isolated
TINK_HOME and temp git project; the sibling `tink` binary is reached through a
PATH shim. Artifact: target/e2e/use.json. Exit 1 on any failure.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
ARTIFACT = REPO / "target" / "e2e" / "use.json"
REAL_LIB = Path.home() / ".tink-library" / "skills"
TINK = os.environ.get("TINK_BIN") or str(REPO.parent / "tink" / "target" / "debug" / "tink")
LIVE_SKILLS = ["eli5", "blast-radius", "epistemic-matrix", "teach", "technical-writing", "tdd"]

BLOCK = "# Project\n\n<!-- tink:rules begin skillset=demo-skillset digest=ab12 -->\nRules.\n<!-- tink:rules end -->\n"

results: list[dict] = []


def record(name: str, ok: bool, detail: str = "", skipped: bool = False) -> None:
    results.append({"case": name, "status": "skip" if skipped else ("pass" if ok else "fail"), "detail": detail})
    print(f"{'SKIP' if skipped else 'PASS' if ok else 'FAIL'}  {name}  {detail}")


class Sandbox:
    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="tink-route-use-e2e-")).resolve()
        self.home = self.root / "home"
        (self.home / "skills").mkdir(parents=True)
        (self.home / "layout.json").write_text('{\n  "kind": "tink-skill-inventory"\n}\n')
        shim = self.root / "shim"
        shim.mkdir()
        os.symlink(TINK, shim / "tink")
        self.proj = self.root / "proj"
        self.proj.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=self.proj, check=True)
        self.env = dict(os.environ, TINK_HOME=str(self.home), PYTHONPATH=str(REPO / "src"),
                        PATH=f"{shim}{os.pathsep}/usr/bin:/bin")
        self.env.pop("TINK_ROUTE_RECEIPT", None)

    def add_skill(self, name: str, desc: str, body: str) -> None:
        d = self.home / "skills" / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n")

    def approve_all(self) -> None:
        subprocess.run([TINK, "library", "approve", "--all"], cwd=self.proj, env=self.env,
                       capture_output=True, check=True)

    def cli(self, *args: str, key: bool = True) -> tuple[int, str, str, float]:
        env = dict(self.env)
        if not key:
            env.pop("TYPESAFE_API_KEY", None)
        t = time.time()
        p = subprocess.run([sys.executable, "-c", "import sys; from tink_route.cli import main; sys.exit(main())", *args], cwd=self.proj, env=env,
                           capture_output=True, text=True)
        return p.returncode, p.stdout, p.stderr, round(time.time() - t, 2)

    def close(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)


def offline() -> None:
    sb = Sandbox()
    try:
        sb.add_skill("alpha", "alpha specialist procedure", "Body.")
        sb.approve_all()
        for flag in ("--use", "-i", "--prune", "--multi", "--stage", "--stage-only"):
            rc, out, err, _ = sb.cli(flag, "task")
            record(f"offline: removed flag {flag} is a usage error",
                   rc == 2 and out == "" and err.count("\n") == 2, f"rc={rc}")
        rc, out, err, _ = sb.cli("--strict", "task")
        record("offline: --strict is gone (usage error)",
               rc == 2 and out == "" and "--strict" in err and err.count("\n") == 2, f"rc={rc}")
        rc, out, err, _ = sb.cli("--anywhere", "--skillset", "x", "task")
        record("offline: --anywhere with --skillset is a usage error",
               rc == 2 and out == "" and "--anywhere" in err and err.count("\n") == 2, f"rc={rc}")
        for label, text in (("unbalanced", "<!-- tink:rules begin skillset=demo-skillset digest=ab -->\nx\n"), ("unknown skillset", BLOCK)):
            (sb.proj / "AGENTS.md").write_text(text)
            rc, out, err, _ = sb.cli("task", key=False)
            record(f"offline: {label} AGENTS.md block is never read (only the missing key fails)",
                   rc == 2 and "tink:rules" not in out and "AGENTS.md names" not in out and "proceed without a skill" in out, f"rc={rc} {out[:80]}")
        (sb.proj / "AGENTS.md").unlink()
        rc, out, err, _ = sb.cli("--help")
        record("offline: --help documents the AGENTS.md line, whole-library default, no removed flags",
               rc == 0 and 'tink-route "<what you need>" and follow the output' in out
               and "<stage>" not in out and "--strict" not in out and "--anywhere" in out
               and "non-zero" in out and "[deprecated]" not in out and "--use" not in out)
        before = sorted(p.relative_to(sb.root).as_posix() for p in sb.root.rglob("*"))
        rc, out, err, _ = sb.cli("--pick", "--json", "task", key=False)
        after = sorted(p.relative_to(sb.root).as_posix() for p in sb.root.rglob("*"))
        record("offline: --pick without API key exits 2 and writes nothing",
               rc == 2 and json.loads(out)["status"] == "error" and before == after, f"rc={rc}")
        rp = sb.root / "receipts.jsonl"
        env_key = os.environ.get("TYPESAFE_API_KEY")
        rc, out, err, _ = sb.cli("--receipt", str(rp), "task", key=False)
        lines = rp.read_text().splitlines() if rp.exists() else []
        rec = json.loads(lines[0]) if lines else {}
        record("offline: missing API key fails open (exit 2, receipt error)",
               rc == 2 and "proceed without" in out and rec.get("status") == "error"
               and rec.get("reason") == "no_api_key", f"rc={rc} receipt={rec.get('reason')}")
        rc, out, err, _ = sb.cli("--json", "task", key=False)
        d = json.loads(out)
        record("offline: --json error shape", rc == 2 and d["status"] == "error" and d["content"] is None)
        _ = env_key
    finally:
        sb.close()


def live() -> None:
    if not os.environ.get("TYPESAFE_API_KEY"):
        record("live: router cases", True, "TYPESAFE_API_KEY not set", skipped=True)
        return
    sb = Sandbox()
    try:
        for n in LIVE_SKILLS:
            src = REAL_LIB / n
            if src.is_dir() and not src.is_symlink():
                shutil.copytree(src, sb.home / "skills" / n, symlinks=False)
        sb.approve_all()
        rp = sb.root / "live.jsonl"

        rc, out, err, secs = sb.cli("--pick", "--json",
                                    "Explain how DNS resolution works like I'm five years old")
        d = json.loads(out) if out.strip().startswith("{") else {}
        record("live: --pick names eli5 and leaves no trace",
               rc == 0 and d.get("winner") == "eli5" and not (sb.proj / ".tink").exists()
               and not rp.exists(), f"rc={rc} winner={d.get('winner')} {secs}s")

        rc, out, err, secs = sb.cli("--json", "--receipt", str(rp),
                                    "Explain how DNS resolution works like I'm five years old")
        d = json.loads(out) if out.strip().startswith("{") else {}
        record("live: clear ELI5 prompt delivers eli5", rc == 0 and d.get("skill") == "eli5"
               and d.get("delivery") == "inline" and bool(d.get("content")), f"rc={rc} skill={d.get('skill')} {secs}s")

        rc, out, err, secs = sb.cli("--receipt", str(rp), "What is the weather in Paris today?")
        record("live: unrelated prompt exits 1", rc == 1 and out.startswith("No specialist skill applies"),
               f"rc={rc} {secs}s")

        sd = sb.home / "skillsets"
        sd.mkdir(exist_ok=True)
        (sd / "demo-skillset.json").write_text(json.dumps({
            "source": "x", "revision": "r", "sourceRoot": "s",
            "members": ["eli5", "blast-radius"], "required": ["blast-radius"]}))
        rc, out, err, secs = sb.cli("--json", "--skillset", "demo", "--receipt", str(rp),
                                    "assess what this change could break and its blast radius")
        d = json.loads(out) if out.strip().startswith("{") else {}
        record("live: required skill excluded from candidates", d.get("skill") != "blast-radius" and rc in (0, 1),
               f"rc={rc} skill={d.get('skill')} {secs}s")

        lines = [json.loads(l) for l in rp.read_text().splitlines()] if rp.exists() else []
        record("live: receipts append one line per call", len(lines) == 3, f"lines={len(lines)}")

        # Explicit shelf (--skillset): project pin `demo` = eli5 + teach; blast-radius is off-shelf.
        pins = sb.proj / ".tink" / "skillsets"
        pins.mkdir(parents=True, exist_ok=True)
        (pins / "demo-skillset.json").write_text(json.dumps({
            "source": "x", "revision": "r", "sourceRoot": "s", "members": ["eli5", "teach"]}))
        (sd / "demo-skillset.json").unlink()

        rc, out, err, secs = sb.cli("--skillset", "demo-skillset", "--json", "Explain how DNS resolution works like I'm five years old")
        d = json.loads(out) if out.strip().startswith("{") else {}
        record("live: an explicit shelf delivers its skill", rc == 0 and d.get("skill") == "eli5"
               and d.get("scope") == "skillset" and d.get("hint") is None, f"rc={rc} skill={d.get('skill')} {secs}s")

        before = sorted(p.relative_to(sb.proj).as_posix() for p in (sb.proj / ".tink").rglob("*"))
        rc, out, err, secs = sb.cli("--skillset", "demo-skillset", "assess what this change could break and its blast radius")
        after = sorted(p.relative_to(sb.proj).as_posix() for p in (sb.proj / ".tink").rglob("*"))
        record("live: off-shelf fit exits 1 with a Hint and delivers nothing",
               rc == 1 and "on the demo-skillset shelf" in out and "Hint: blast-radius fits but" in out
               and "it was not delivered" in out and "# tink skill" not in out and before == after
               and not (sb.proj / ".tink" / ".active" / "blast-radius").exists(), f"rc={rc} {secs}s\n{out}")

        rc, out, err, secs = sb.cli("--skillset", "demo-skillset", "What is the weather in Paris today?")
        record("live: unrelated task exits 1 with no hint",
               rc == 1 and "on the demo-skillset shelf" in out and "Hint" not in out, f"rc={rc} {secs}s")
    finally:
        sb.close()


def main() -> int:
    if not os.access(TINK, os.X_OK):
        print(f"tink binary not found at {TINK}; build it: cd ../tink && cargo build -q", file=sys.stderr)
        return 1
    offline()
    live()
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    failed = [r for r in results if r["status"] == "fail"]
    ARTIFACT.write_text(json.dumps({"passed": sum(r["status"] == "pass" for r in results),
                                    "failed": len(failed),
                                    "skipped": sum(r["status"] == "skip" for r in results),
                                    "results": results}, indent=2) + "\n")
    print(f"artifact: {ARTIFACT}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
