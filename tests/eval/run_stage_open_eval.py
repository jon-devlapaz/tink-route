#!/usr/bin/env python3
"""Stage-open routing eval: the input document of a stage -> `tink-route --pick --anywhere` -> metrics.

This measures what a launcher does at stage open: hand the whole prior document to the router and
accept its pick or its abstention. Cases (stage_open_cases.jsonl): plain, paraphrase (cue words
removed), multi_need, and none (no specialist wanted). Optional needle variants bury one plain
document inside 20k/100k characters of unrelated text.

Run:   TYPESAFE_API_KEY=... python3 tests/eval/run_stage_open_eval.py [--variants doc,stage] [--needle] [--limit N]
Check: python3 tests/eval/run_stage_open_eval.py --check      (no network: validates the case file)
Reads the real library read-only (copies to a scratch dir, dropping symlinked entries).
Artifact (overwritten): target/eval/stage-open-eval.json and .md
"""
import argparse
import concurrent.futures as cf
import json
import os
import re
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import run_eval as base  # noqa: E402  (scratch_library, route)

CASES = Path(__file__).with_name("stage_open_cases.jsonl")
FILLER = Path(__file__).with_name("stage_open_filler.json")
OUT = base.OUT


def load_cases():
    return [json.loads(line) for line in CASES.read_text().splitlines() if line.strip()]


def check(cases, skills):
    problems = []
    seen = set()
    for c in cases:
        if c["id"] in seen:
            problems.append(f"duplicate id {c['id']}")
        seen.add(c["id"])
        if c["kind"] not in ("plain", "paraphrase", "multi_need", "none"):
            problems.append(f"{c['id']}: bad kind {c['kind']}")
        if (c["kind"] == "none") != (not c["gold"]):
            problems.append(f"{c['id']}: gold must be empty exactly for kind none")
        for g in c["gold"]:
            if g not in skills:
                problems.append(f"{c['id']}: gold {g} is not in the library")
            if "-" in g and re.search(re.escape(g), c["doc"], re.I):  # hyphenated ids only: how/why/herdr are ordinary words or product names
                problems.append(f"{c['id']}: document names its gold skill {g} (cue leak)")
    return problems


def filler_text(n):
    bank = json.loads(FILLER.read_text())["filler"]
    out, i = [], 0
    while sum(map(len, out)) < n:
        out.append(f"Section {i + 1}. " + bank[i % len(bank)].replace("thirty", str(30 + i % 9)).replace("seven", str(7 + i % 3)))
        i += 1
    return "\n\n".join(out)


def bury(doc, length, pos):
    f = filler_text(length)
    cut = {"start": 0, "middle": len(f) // 2, "end": len(f)}[pos]
    cut = max(f.rfind("\n\n", 0, cut), 0) if cut else 0
    return f[:cut] + ("\n\n" if cut else "") + "Requirement note: " + doc + "\n\n" + f[cut:].lstrip()


def summarize(rows):
    sp = [r for r in rows if r["kind"] != "none"]
    nn = [r for r in rows if r["kind"] == "none"]
    routed = [r for r in sp if r["routed"]]
    ok = [r for r in routed if r["winner"] in r["gold"]]
    return {"n_specific": len(sp), "routed": len(routed), "correct": len(ok), "wrong": len(routed) - len(ok),
            "abstained": len(sp) - len(routed), "n_none": len(nn), "false_route": sum(1 for r in nn if r["routed"]),
            "precision": round(len(ok) / len(routed), 3) if routed else None,
            "recall": round(len(ok) / len(sp), 3) if sp else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--variants", default="doc,stage", help="doc = document only; stage = 'Stage: X.' prefixed")
    ap.add_argument("--needle", action="store_true", help="also bury 8 plain documents in 20k/100k chars of filler")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    cases = load_cases()
    with tempfile.TemporaryDirectory() as tmp:
        lib = Path(tmp)
        skills = base.scratch_library(lib)
        problems = check(cases, skills)
        if args.check:
            print(f"{len(cases)} cases, {len(skills)} library skills")
            for p in problems:
                print("PROBLEM:", p)
            return 1 if problems else 0
        if problems:
            print("\n".join(problems))
            return 2
        if args.limit:
            cases = cases[: args.limit]
        jobs = []
        for v in args.variants.split(","):
            for c in cases:
                task = c["doc"] if v == "doc" else f"Stage: {c['stage']}. {c['doc']}"
                jobs.append((f"{v}", c["id"], c["kind"], c["gold"], task))
        if args.needle:
            byid = {c["id"]: c for c in cases}
            for cid in json.loads(FILLER.read_text())["needle_ids"]:
                c = byid.get(cid)
                if not c:
                    continue
                for L in (20000, 100000):
                    for pos in ("start", "middle", "end"):
                        jobs.append((f"needle{L // 1000}k-{pos}", cid, "plain", c["gold"], f"Stage: {c['stage']}. " + bury(c["doc"], L, pos)))

        def go(j):
            variant, cid, kind, gold, task = j
            r, dt = base.route(lib, task, ["--anywhere"])
            return {"variant": variant, "id": cid, "kind": kind, "gold": gold, "routed": r.get("status") == "routed",
                    "winner": r.get("winner"), "confidence": r.get("confidence"), "status": r.get("status"), "seconds": round(dt, 2)}

        with cf.ThreadPoolExecutor(args.workers) as ex:
            rows = list(ex.map(go, jobs))
    by = {}
    for r in rows:
        by.setdefault(r["variant"], []).append(r)
    summary = {v: summarize(rs) for v, rs in by.items()}
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "stage-open-eval.json").write_text(json.dumps({"summary": summary, "rows": rows}, indent=1))
    lines = ["# Stage-open routing eval", "", "| variant | specific | routed | correct | wrong | abstained | none false-routed | precision | recall |", "|---|---|---|---|---|---|---|---|---|"]
    for v, s in summary.items():
        lines.append(f"| {v} | {s['n_specific']} | {s['routed']} | {s['correct']} | {s['wrong']} | {s['abstained']} | {s['false_route']}/{s['n_none']} | {s['precision']} | {s['recall']} |")
    wrong = [r for r in rows if r["kind"] != "none" and r["routed"] and r["winner"] not in r["gold"]]
    if wrong:
        lines += ["", "Wrong picks:"] + [f"- {r['variant']} {r['id']}: {r['winner']} (want {r['gold']}) conf {r['confidence']}" for r in wrong]
    (OUT / "stage-open-eval.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    print(f"\nartifact: {OUT / 'stage-open-eval.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
