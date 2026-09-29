#!/usr/bin/env python3
"""E2E for `tink-route --use`, driving the real CLI as a subprocess.

Offline cases need no router. Live cases (real Jev router, <= 15 API calls) are
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
        for flag in ("-i", "--prune", "--multi"):
            rc, out, err, _ = sb.cli("--use", flag, "task")
            record(f"offline: --use {flag} is a usage error", rc == 2 and out == "" and err.count("\n") == 1,
                   f"rc={rc}")
        rc, out, err, _ = sb.cli("--help")
        record("offline: --help documents --use snippet",
               rc == 0 and "tink-route --use --stage <stage>" in out and "non-zero" in out)
        rp = sb.root / "receipts.jsonl"
        env_key = os.environ.get("TYPESAFE_API_KEY")
        rc, out, err, _ = sb.cli("--use", "--receipt", str(rp), "task", key=False)
        lines = rp.read_text().splitlines() if rp.exists() else []
        rec = json.loads(lines[0]) if lines else {}
        record("offline: missing API key fails open (exit 2, receipt error)",
               rc == 2 and "proceed without" in out and rec.get("status") == "error"
               and rec.get("reason") == "no_api_key", f"rc={rc} receipt={rec.get('reason')}")
        rc, out, err, _ = sb.cli("--use", "--json", "task", key=False)
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

        rc, out, err, secs = sb.cli("--use", "--json", "--receipt", str(rp),
                                    "Explain how DNS resolution works like I'm five years old")
        d = json.loads(out) if out.strip().startswith("{") else {}
        record("live: clear ELI5 prompt delivers eli5", rc == 0 and d.get("skill") == "eli5"
               and d.get("delivery") == "inline" and bool(d.get("content")), f"rc={rc} skill={d.get('skill')} {secs}s")

        rc, out, err, secs = sb.cli("--use", "--receipt", str(rp), "What is the weather in Paris today?")
        record("live: unrelated prompt exits 1", rc == 1 and out.startswith("No specialist skill applies"),
               f"rc={rc} {secs}s")

        sd = sb.home / "skillsets"
        sd.mkdir(exist_ok=True)
        (sd / "demo-skillset.json").write_text(json.dumps({
            "source": "x", "revision": "r", "sourceRoot": "s",
            "members": ["eli5", "blast-radius"], "required": ["blast-radius"]}))
        rc, out, err, secs = sb.cli("--use", "--json", "--skillset", "demo", "--receipt", str(rp),
                                    "assess what this change could break and its blast radius")
        d = json.loads(out) if out.strip().startswith("{") else {}
        record("live: required skill excluded from candidates", d.get("skill") != "blast-radius" and rc in (0, 1),
               f"rc={rc} skill={d.get('skill')} {secs}s")

        lines = [json.loads(l) for l in rp.read_text().splitlines()] if rp.exists() else []
        record("live: receipts append one line per call", len(lines) == 3, f"lines={len(lines)}")
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
