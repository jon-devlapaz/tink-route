#!/usr/bin/env python3
"""E2E: README happy-paths route with default gates (issues #6, #7).

Builds a fixture library containing the README winner skills, routes the
three documented happy-path tasks with default tri-gate + rerank, and asserts
each routes to its documented winner. Requires TYPESAFE_API_KEY.

Produces a repeatable artifact: /tmp/tink-e2e-happy-paths.json
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

SKILLS = {
    "threejs-shaders": (
        "Create WebGL particle simulations with custom GLSL vertex and fragment "
        "shaders in Three.js, including shader materials, uniforms, and GPU particle systems.",
        "# ThreeJS Shaders\nWrite GLSL shaders, ShaderMaterial setups, and particle buffers.",
    ),
    "cro": (
        "Audit e-commerce checkout flows to optimize conversion rate through funnel "
        "analysis, form optimization, A/B test design, and friction reduction.",
        "# CRO\nAnalyze checkout funnels, identify drop-off, optimize forms, design experiments.",
    ),
    "improve-codebase-architecture": (
        "Audit codebase architecture and produce architectural improvement plans covering "
        "modularity, layering, coupling, and refactoring strategy.",
        "# Architecture\nReview module boundaries and produce a prioritized improvement plan.",
    ),
    "plain-notes": (
        "Take plain meeting notes with action items and decisions in markdown format.",
        "# Notes\nSimple markdown meeting notes.",
    ),
}

CASES = [
    ("Create WebGL particle simulation with custom GLSL shaders", "threejs-shaders"),
    ("Audit e-commerce checkout flow to optimize conversion rate", "cro"),
    ("Audit this codebase architecture", "improve-codebase-architecture"),
]

REPO = Path(__file__).resolve().parent.parent
ARTIFACT = Path("/tmp/tink-e2e-happy-paths.json")


def build_library(root: Path) -> None:
    for name, (desc, body) in SKILLS.items():
        d = root / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {desc}\n---\n{body}\n", encoding="utf-8"
        )


def route(lib: Path, task: str) -> dict:
    env = dict(
        os.environ,
        PYTHONPATH=str(REPO / "src"),
        TINK_E2E_LIB=str(lib),
        TINK_E2E_TASK=task,
    )
    p = subprocess.run(
        [sys.executable, "-c",
         "import os, sys; sys.argv=['tink-route', '--json', '--library', "
         "os.environ['TINK_E2E_LIB'], os.environ['TINK_E2E_TASK']]; "
         "from tink_route.cli import main; main()"],
        capture_output=True, text=True, cwd=str(REPO), env=env, check=False,
    )
    try:
        return json.loads(p.stdout)
    except json.JSONDecodeError:
        return {"status": "error", "stdout": p.stdout, "stderr": p.stderr}


def main() -> int:
    if not os.environ.get("TYPESAFE_API_KEY"):
        print("SKIP: TYPESAFE_API_KEY not set", file=sys.stderr)
        return 0
    with tempfile.TemporaryDirectory(prefix="tink-e2e-lib-") as tmp:
        lib = Path(tmp)
        build_library(lib)
        results = []
        failures = 0
        for task, expected in CASES:
            r = route(lib, task)
            ok = r.get("status") == "routed" and r.get("winner") == expected
            failures += not ok
            results.append({
                "task": task, "expected": expected,
                "status": r.get("status"), "winner": r.get("winner"),
                "probability": r.get("probability"),
                "specialist_noul": r.get("specialist_noul"),
                "fits": r.get("fits"), "pass": ok,
            })
            print(f"[{'PASS' if ok else 'FAIL'}] {task!r:.60} -> {r.get('status')}/{r.get('winner')} fits={r.get('fits')}")
        ARTIFACT.write_text(json.dumps({"results": results}, indent=2), encoding="utf-8")
        print(f"Artifact: {ARTIFACT}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
