#!/usr/bin/env python3
"""Routing eval: labelled prompts -> tink-route --pick -> metrics, with Jev as independent judge.

Measures, per router config: latency p50/p95, hit / acceptable / misroute / miss rates on
positives, false-injection rate on negatives. For every routed prompt that is NOT the labelled
expected skill, a second Jev model (default jev-latest, different from the router's pinned
model) judges "is this skill actually needed for this task?" so label gaps do not count as
router errors and real misroutes are separated from defensible alternatives.

Run:  TYPESAFE_API_KEY=... python3 tests/eval/run_eval.py [--configs default,no-rerank] [--limit N]
Reads the real library read-only (copies to a scratch dir, dropping symlinked entries).
Artifact (overwritten): target/eval/routing-eval.json   (+ .md summary beside it)
"""
import argparse
import json
import os
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SRC = REPO / "src"
PROMPTS = Path(__file__).with_name("prompts.jsonl")
OUT = REPO / "target" / "eval"
LIB = Path(os.environ.get("TINK_HOME", Path.home() / ".tink-library")) / "skills"
API = "https://api.typesafe.ai/v1/systemone"
CONFIGS = {
    "default": [],
    "no-rerank": ["--no-rerank"],
    "no-gate": ["--no-tri-gate"],
    "bare": ["--no-tri-gate", "--no-rerank"],
}


def scratch_library(dst: Path) -> dict:
    """Copy the library minus symlinked entries; return {name: description}."""
    descs = {}
    for d in sorted(LIB.iterdir()):
        if d.name.startswith(".") or d.is_symlink() or not (d / "SKILL.md").is_file():
            continue
        shutil.copytree(d, dst / d.name, symlinks=False)
        text = (d / "SKILL.md").read_text(encoding="utf-8-sig", errors="replace")
        desc = ""
        for line in text.splitlines():
            if line.startswith("description:"):
                desc = line.split(":", 1)[1].strip().strip(">|").strip()
        descs[d.name] = desc or d.name
    return descs


def route(lib: Path, task: str, extra: list[str]) -> tuple[dict, float]:
    env = dict(os.environ, PYTHONPATH=str(SRC))
    t0 = time.perf_counter()
    p = subprocess.run(
        [sys.executable, "-c", "import sys; from tink_route.cli import main; sys.exit(main())",
         "--pick", "--json", "--library", str(lib), *extra, task],
        capture_output=True, text=True, env=env, cwd=str(REPO), check=False,
    )
    dt = time.perf_counter() - t0
    try:
        return json.loads(p.stdout), dt
    except json.JSONDecodeError:
        return {"status": "error", "raw": (p.stdout + p.stderr)[:200]}, dt


def judge(task: str, skill: str, desc: str, model: str) -> dict:
    """Independent Jev verdict: does this task genuinely need this skill?"""
    body = {
        "state": f"Task from a user to a coding agent:\n{task}\n\nCandidate skill '{skill}': {desc[:600]}",
        "model": model,
        "questions": {
            "needed": {
                "type": "choice",
                "instructions": "Would loading this skill's instructions materially help do this task well?",
                "criteria": {
                    "needed": "The skill's described purpose is directly applicable to this task and would improve the result.",
                    "not_needed": "The task can be done equally well without this skill, or the skill is about something else.",
                },
            }
        },
    }
    req = urllib.request.Request(
        API, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {os.environ['TYPESAFE_API_KEY']}", "Content-Type": "application/json",
                 "User-Agent": "tink-route-eval/1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            ans = json.load(r)
        a = ans.get("needed") or ans.get("answers", {}).get("needed") or {}
        return {"verdict": a.get("choice"), "confidence": a.get("confidence")}
    except Exception as exc:  # judge failure must not sink the run
        return {"verdict": None, "error": str(exc)[:120]}


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(round(q * (len(xs) - 1))))] if xs else None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--configs", default="default,no-rerank")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--judge-model", default="jev-latest")
    ap.add_argument("--no-judge", action="store_true")
    a = ap.parse_args()
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("TYPESAFE_API_KEY required", file=sys.stderr)
        return 2
    rows = [json.loads(l) for l in PROMPTS.read_text().splitlines() if l.strip()]
    if a.limit:
        rows = rows[: a.limit]
    report = {"ran_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "n_prompts": len(rows), "configs": {}}
    with tempfile.TemporaryDirectory(prefix="tink-eval-lib-") as t:
        lib = Path(t)
        descs = scratch_library(lib)
        report["library_skills"] = len(descs)
        for cname in a.configs.split(","):
            extra = CONFIGS[cname]
            per, lat = [], []
            for r in rows:
                res, dt = route(lib, r["prompt"], extra)
                lat.append(dt)
                routed = res.get("status") == "routed"
                winner = res.get("winner") if routed else None
                exp, acc = r["expected"], set(r["acceptable"])
                rec = {"id": r["id"], "category": r["category"], "expected": exp, "winner": winner,
                       "status": res.get("status"), "confidence": res.get("confidence"),
                       "noul": res.get("specialist_noul"), "latency_s": round(dt, 3)}
                if exp is None:
                    rec["outcome"] = "correct_abstain" if not routed else "false_injection"
                elif winner == exp:
                    rec["outcome"] = "hit"
                elif winner in acc:
                    rec["outcome"] = "acceptable"
                elif winner is None:
                    rec["outcome"] = "miss"
                else:
                    rec["outcome"] = "other_route"
                if winner and rec["outcome"] in ("false_injection", "other_route") and not a.no_judge:
                    rec["judge"] = judge(r["prompt"], winner, descs.get(winner, ""), a.judge_model)
                per.append(rec)
            oc = {}
            for rec in per:
                oc[rec["outcome"]] = oc.get(rec["outcome"], 0) + 1
            pos = [x for x in per if x["expected"]]
            neg = [x for x in per if not x["expected"]]
            judged_ok = sum(1 for x in per if x.get("judge", {}).get("verdict") == "needed")
            report["configs"][cname] = {
                "flags": extra,
                "latency_s": {"p50": round(pct(lat, .5), 3), "p95": round(pct(lat, .95), 3), "max": round(max(lat), 3),
                              "mean": round(statistics.mean(lat), 3)},
                "outcomes": oc,
                "positive_hit_or_acceptable": round(sum(1 for x in pos if x["outcome"] in ("hit", "acceptable")) / max(1, len(pos)), 3),
                "positive_miss": round(sum(1 for x in pos if x["outcome"] == "miss") / max(1, len(pos)), 3),
                "positive_other_route": round(sum(1 for x in pos if x["outcome"] == "other_route") / max(1, len(pos)), 3),
                "false_injection_rate": round(sum(1 for x in neg if x["outcome"] == "false_injection") / max(1, len(neg)), 3),
                "judge_says_needed_among_disagreements": judged_ok,
                "results": per,
            }
            c = report["configs"][cname]
            print(f"[{cname}] p50={c['latency_s']['p50']}s p95={c['latency_s']['p95']}s hit+acc={c['positive_hit_or_acceptable']} "
                  f"miss={c['positive_miss']} other={c['positive_other_route']} false_inj={c['false_injection_rate']} "
                  f"judge_needed={judged_ok}")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "routing-eval.json").write_text(json.dumps(report, indent=2))
    print(f"artifact: {(OUT / 'routing-eval.json').relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
