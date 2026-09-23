"""Command line interface for tink-route."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from . import __version__
from .adapters.executor import DefaultSubprocessExecutor
from .adapters.ledger import FilesystemLedger
from .client import DEFAULT_MODEL, DEFAULT_THRESHOLD, JevRouterClient
from .core.constants import FITS_THRESHOLD, MULTI_DEFAULT_TOP_K
from .core.engine import RoutingEngine
from .core.models import InstallOutcome
from .ephemeral import (
    _record_ephemeral_skill_locked,
    ephemeral_ledger_lock,
    prune_ephemeral_skills,
)
from .metadata import load_library_skills

DEFAULT_LIBRARY_PATH = Path.home() / ".tink" / "skills"
_DEFAULT_EXECUTOR = DefaultSubprocessExecutor()
_DEFAULT_LEDGER = FilesystemLedger()
_DEFAULT_ENGINE = RoutingEngine(executor=_DEFAULT_EXECUTOR, ledger=_DEFAULT_LEDGER)


def install_skill(skill_name: str, project_dir: Optional[Path] = None) -> InstallOutcome:
    """Execute `tink skill add -- <skill_name>` and inspect installed assets."""
    cwd = project_dir or Path.cwd()
    with ephemeral_ledger_lock(cwd):
        return _install_skill_locked(skill_name, cwd)


def _install_skill_locked(skill_name: str, cwd: Path) -> InstallOutcome:
    """Install while holding project lock, validating path containment."""
    return _DEFAULT_ENGINE.install_skill_locked(skill_name, cwd)


def _install_and_track(skill_name: str, project_dir: Path, track: bool) -> Any:
    """Install and update ownership while holding one project transaction lock."""
    with ephemeral_ledger_lock(project_dir):
        outcome = _install_skill_locked(skill_name, project_dir)
        is_success = outcome.success if hasattr(outcome, "success") else bool(outcome.get("success", False))
        is_pre_existing = outcome.was_pre_existing if hasattr(outcome, "was_pre_existing") else bool(outcome.get("was_pre_existing", False))
        if not is_success or not track or is_pre_existing:
            return outcome
        try:
            _record_ephemeral_skill_locked(project_dir, skill_name)
        except Exception as exc:
            msg = f"Failed to record ephemeral ledger: {exc}"
            if hasattr(outcome, "tracking_error"):
                outcome.tracking_error = msg
            else:
                outcome["tracking_error"] = msg
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
    parser.add_argument(
        "--tri-gate",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use three orthogonal Stage 1 noul questions instead of a single specialist gate (default: true).",
    )
    parser.add_argument(
        "--rerank",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Rerank the top Stage 2 candidates using SKILL.md excerpts and fits nouls (default: true).",
    )
    parser.add_argument(
        "--fits-threshold",
        type=float,
        default=FITS_THRESHOLD,
        help=f"Minimum per-skill fits noul required to accept a reranked winner (default: {FITS_THRESHOLD}).",
    )
    parser.add_argument(
        "--multi",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Return a ranked list of qualifying skills instead of a single winner.",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=MULTI_DEFAULT_TOP_K,
        help=f"Maximum skills to return with --multi (default: {MULTI_DEFAULT_TOP_K}).",
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

        output_res = res.to_dict() if hasattr(res, "to_dict") else dict(res)
        if args.json:
            print(json.dumps(output_res, indent=2))
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
                    for err_item in res["errors"]:
                        print(f"Error removing {err_item['skill']}: {err_item['error']}", file=sys.stderr)
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
    engine = RoutingEngine(
        client=client,
        executor=_DEFAULT_EXECUTOR,
        ledger=_DEFAULT_LEDGER,
        install_handler=_install_and_track,
    )
    try:
        result = engine.route(
            task=args.task,
            library=skills,
            threshold=args.threshold,
            install=args.install,
            ephemeral=args.ephemeral,
            project_dir=Path.cwd(),
            tri_gate=args.tri_gate,
            rerank=args.rerank,
            fits_threshold=args.fits_threshold,
            multi=args.multi,
            top_k=args.top_k,
        )
    except Exception as e:
        err = {"error": str(e)}
        if args.json:
            print(json.dumps(err))
        else:
            print(f"Routing failure: {e}", file=sys.stderr)
        return 2

    install_failed = False
    if result["status"] in ("routed", "multi_routed") and args.install:
        if not result.get("installed") or result.get("tracking_error"):
            install_failed = True

    output_data = result.to_dict() if hasattr(result, "to_dict") else dict(result)
    if args.json:
        print(json.dumps(output_data, indent=2))
    else:
        status = result["status"]
        if status in ("no_skill_needed", "no_match"):
            print(f"Status: {status} (specialist_noul: {result['specialist_noul']:.2f}).")
            print("Standard coding tools and models are sufficient.")
        elif status == "multi_routed":
            cands = result.get("candidates", [])
            print(f"Recommended Skills ({len(cands)}):")
            for idx, cand in enumerate(cands, start=1):
                print(f"  {idx}. {cand['skill']} (p={cand['probability']:.2f})")
            if result.get("installed") and not result.get("tracking_error"):
                print(f"Installed winner: {result.get('skill_path')}")
                print("Activation: Ready for immediate direct reading (no restart required).")
            elif args.install and result.get("tracking_error"):
                print(f"Installation succeeded but ownership tracking failed: {result['tracking_error']}", file=sys.stderr)
            elif args.install and not result.get("installed"):
                print(f"Installation failed: {result.get('install_error', '')}", file=sys.stderr)
            else:
                print(f"To install winners run: tink skill add {result['winner']}")
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
    return 0 if result["status"] in ("routed", "multi_routed") else 1


if __name__ == "__main__":
    sys.exit(main())
