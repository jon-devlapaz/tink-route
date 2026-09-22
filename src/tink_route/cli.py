import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

from . import __version__
from .client import DEFAULT_MODEL, DEFAULT_THRESHOLD, JevRouterClient
from .ephemeral import _record_ephemeral_skill_locked, ephemeral_ledger_lock, prune_ephemeral_skills
from .metadata import load_library_skills

DEFAULT_LIBRARY_PATH = Path.home() / ".tink" / "skills"


def install_skill(skill_name: str, project_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Execute `tink skill add <skill_name>` and inspect installed assets."""
    cwd = project_dir or Path.cwd()
    with ephemeral_ledger_lock(cwd):
        return _install_skill_locked(skill_name, cwd)


def _install_skill_locked(skill_name: str, cwd: Path) -> Dict[str, Any]:
    """Install while the caller holds the project ownership lock."""
    skill_dir = cwd / ".agents" / "skills" / skill_name
    was_pre_existing = (skill_dir / "SKILL.md").is_file()

    try:
        res = subprocess.run(
            ["tink", "skill", "add", skill_name],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )
        success = (res.returncode == 0)
        stdout = res.stdout.strip()
        stderr = res.stderr.strip()
        code = res.returncode
    except FileNotFoundError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "tink executable not found in PATH",
            "code": 2,
            "skill_path": None,
            "references": [],
            "scripts": [],
            "was_pre_existing": was_pre_existing,
        }

    skill_rel_path = f".agents/skills/{skill_name}/SKILL.md"

    references = []
    scripts = []
    if success and skill_dir.is_dir():
        ref_dir = skill_dir / "references"
        if ref_dir.is_dir():
            references = [
                p.relative_to(skill_dir).as_posix()
                for p in sorted(ref_dir.iterdir())
                if p.is_file() and not p.name.startswith(".")
            ]
        scr_dir = skill_dir / "scripts"
        if scr_dir.is_dir():
            scripts = [
                p.relative_to(skill_dir).as_posix()
                for p in sorted(scr_dir.iterdir())
                if p.is_file() and not p.name.startswith(".")
            ]

    return {
        "success": success,
        "stdout": stdout,
        "stderr": stderr,
        "code": code,
        "skill_path": skill_rel_path if success else None,
        "references": references,
        "scripts": scripts,
        "was_pre_existing": was_pre_existing,
    }


def _install_and_track(skill_name: str, project_dir: Path, track: bool) -> Dict[str, Any]:
    """Install and update ownership while holding one project transaction lock."""
    with ephemeral_ledger_lock(project_dir):
        outcome = _install_skill_locked(skill_name, project_dir)
        if not outcome["success"] or not track or outcome.get("was_pre_existing"):
            return outcome
        try:
            _record_ephemeral_skill_locked(project_dir, skill_name)
        except Exception as exc:
            outcome["tracking_error"] = f"Failed to record ephemeral ledger: {exc}"
        return outcome


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tink-route",
        description="Dynamic Agent Skill Router using TypeSafe Jev.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "-v",
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "task",
        nargs="?",
        help="The task description to evaluate, or 'prune' to sweep ephemeral skills.",
    )
    parser.add_argument(
        "--prune",
        action="store_true",
        help="Prune ephemeral/unpinned skills from .agents/skills/.",
    )
    parser.add_argument(
        "--all-unpinned",
        action="store_true",
        help="In prune mode, sweep all unpinned skills (including manual installs) rather than ledger-only.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview skills eligible for pruning without removing them.",
    )
    parser.add_argument(
        "--ephemeral",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Track installed skill in .tink/ephemeral.json for automatic pruning (default: true).",
    )
    parser.add_argument(
        "-i",
        "--install",
        action="store_true",
        help="Automatically install the winning skill into .agents/skills/ via `tink skill add`.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Minimum confidence/probability threshold (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON.")
    parser.add_argument(
        "--library",
        type=Path,
        default=DEFAULT_LIBRARY_PATH,
        help=f"Path to Tink skill library directory (default: {DEFAULT_LIBRARY_PATH}).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Pinned TypeSafe Jev model identifier (default: {DEFAULT_MODEL}).",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Handle prune command (either `tink-route prune` or `tink-route --prune`)
    if args.task == "prune" or args.prune:
        try:
            res = prune_ephemeral_skills(Path.cwd(), dry_run=args.dry_run, all_unpinned=args.all_unpinned)
        except Exception as e:
            err = {"error": str(e)}
            if args.json:
                print(json.dumps(err, indent=2))
            else:
                print(f"Prune error: {e}", file=sys.stderr)
            return 2

        if args.json:
            print(json.dumps(res, indent=2))
        else:
            if res.get("dry_run"):
                if res["pruned"]:
                    print(f"Eligible for pruning ({res['count']}): {', '.join(res['pruned'])}")
                else:
                    print("No transient skills eligible for pruning.")
            else:
                if res["pruned"]:
                    print(f"Pruned {res['count']} ephemeral skill(s): {', '.join(res['pruned'])}")
                    print("Clean state confirmed in .agents/skills/.")
                else:
                    print("No ephemeral skills to prune.")
                if res.get("errors"):
                    for err in res["errors"]:
                        print(f"Error removing {err['skill']}: {err['error']}", file=sys.stderr)
        if args.dry_run:
            return 0
        if res.get("errors"):
            return 2
        return 0 if res["count"] > 0 else 1

    if not args.task:
        parser.print_help()
        return 1

    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        err = {"error": "TYPESAFE_API_KEY environment variable is not set."}
        if args.json:
            print(json.dumps(err))
        else:
            print(f"Error: {err['error']}", file=sys.stderr)
        return 2

    try:
        skills = load_library_skills(args.library)
    except Exception as e:
        err = {"error": f"Unable to load skill library: {e}"}
        if args.json:
            print(json.dumps(err))
        else:
            print(f"Error: {err['error']}", file=sys.stderr)
        return 2
    if not skills:
        err = {"error": f"No skills found in library directory: {args.library}"}
        if args.json:
            print(json.dumps(err))
        else:
            print(f"Error: {err['error']}", file=sys.stderr)
        return 2

    client = JevRouterClient(api_key=api_key, model=args.model)
    try:
        result = client.route(args.task, skills, threshold=args.threshold)
    except Exception as e:
        err = {"error": str(e)}
        if args.json:
            print(json.dumps(err))
        else:
            print(f"Routing failure: {e}", file=sys.stderr)
        return 2

    # Handle installation if requested and routed
    install_failed = False
    if result["status"] == "routed" and args.install:
        project_dir = Path.cwd()
        try:
            install_res = _install_and_track(result["winner"], project_dir, args.ephemeral)
        except Exception as e:
            install_res = {
                "success": False,
                "stdout": "",
                "stderr": str(e),
                "code": 2,
                "skill_path": None,
                "references": [],
                "scripts": [],
                "was_pre_existing": False,
            }
        result["installed"] = install_res["success"]
        result["skill_path"] = install_res.get("skill_path")
        result["references"] = install_res.get("references", [])
        result["scripts"] = install_res.get("scripts", [])
        result["install_output"] = install_res["stdout"] or install_res["stderr"]
        if install_res["success"]:
            if install_res.get("tracking_error"):
                install_failed = True
                result["tracking_error"] = install_res["tracking_error"]
            else:
                result["activation"] = {
                    "mode": "direct_read",
                    "entrypoint": result.get("skill_path"),
                    "references": result.get("references", []),
                    "scripts": result.get("scripts", []),
                    "restart_required": False,
                    "instruction": "Read SKILL.md directly; mid-session use does not require session restart.",
                }
        else:
            install_failed = True
            result["install_error"] = install_res["stderr"] or "Installation failed"
    elif result["status"] == "routed" and not args.install:
        result["activation"] = {
            "mode": "install_required",
            "entrypoint": None,
            "references": [],
            "scripts": [],
            "restart_required": False,
            "instruction": f"Run 'tink skill add {result['winner']}' to install before reading.",
        }

    if args.json:
        print(json.dumps(result, indent=2))
    else:
        status = result["status"]
        if status in ("no_skill_needed", "no_match"):
            print(f"Status: {status} (specialist_noul: {result['specialist_noul']:.2f}).")
            print("Standard coding tools and models are sufficient.")
        elif status == "routed":
            winner = result["winner"]
            p = result["probability"]
            conf = result["confidence"]
            print(f"Recommended Skill: {winner} (p={p:.2f}, conf={conf:.2f}, noul={result['specialist_noul']:.2f})")
            if result.get("installed") and not result.get("tracking_error"):
                print(f"Installed: {result.get('skill_path')}")
                refs = result.get("references", [])
                print(f"References: {', '.join(refs) if refs else '(none)'}")
                if result.get("scripts"):
                    print(f"Scripts: {', '.join(result['scripts'])}")
                print("Activation: Ready for immediate direct reading (no restart required).")
            elif args.install and result.get("tracking_error"):
                print(f"Installation succeeded but ownership tracking failed: {result['tracking_error']}", file=sys.stderr)
            elif args.install and not result.get("installed"):
                print(f"Installation failed: {result.get('install_error', '')}", file=sys.stderr)
            else:
                print(f"To install run: tink skill add {winner}")
        else:
            top = result.get("top_candidate")
            p = result.get("probability", 0.0)
            runner_up = result.get("runner_up")
            rup_p = result.get("runner_up_probability", 0.0)
            margin = result.get("margin", 0.0)
            if runner_up:
                print(f"Status: uncertain. Top candidate '{top}' (p={p:.2f}) fell below threshold {result['threshold']:.2f}. Runner-up: '{runner_up}' (p={rup_p:.2f}, margin={margin:.2f}).")
            else:
                print(f"Status: uncertain. Top candidate '{top}' (p={p:.2f}) fell below threshold {result['threshold']:.2f}.")

    # Semantic exit code contract:
    # 0 = routed successfully (and installed if -i was passed)
    # 1 = unrouted (no skill needed or decision uncertain)
    # 2 = error (installation failed, API error, missing key/library)
    if install_failed:
        return 2
    return 0 if result["status"] == "routed" else 1


if __name__ == "__main__":
    sys.exit(main())
