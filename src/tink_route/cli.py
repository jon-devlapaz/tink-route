"""Command line interface for tink-route."""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .adapters.executor import DefaultSubprocessExecutor
from .adapters.ledger import default_ledger
from .adapters.client import DEFAULT_MODEL, DEFAULT_THRESHOLD, JevRouterClient
from .core.constants import FITS_THRESHOLD, MULTI_DEFAULT_TOP_K
from .core.engine import RoutingEngine
from .core.models import InstallOutcome, RoutingResult
from .metadata import load_library_skills

DEFAULT_LIBRARY_PATH = Path.home() / ".tink" / "skills"
_DEFAULT_EXECUTOR = DefaultSubprocessExecutor()
_DEFAULT_ENGINE = RoutingEngine(executor=_DEFAULT_EXECUTOR, ledger=default_ledger)


def install_skill(skill_name: str, project_dir: Optional[Path] = None) -> InstallOutcome:
    """Execute `tink skill add -- <skill_name>` and inspect installed assets."""
    return _DEFAULT_ENGINE.install_skill(skill_name, project_dir)


def _install_skill_locked(skill_name: str, cwd: Path) -> InstallOutcome:
    """Install one skill. The caller is responsible for the ledger lock."""
    return _DEFAULT_ENGINE.install_skill_locked(skill_name, cwd)


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
        help="The task description to evaluate, or 'prune', 'update', or 'version'.",
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
    parser.add_argument(
        "--check",
        action="store_true",
        help="With the 'update' task: only check for a newer release, do not install.",
    )
    return parser


def _print_install_followup(result: RoutingResult, installing: bool) -> None:
    if result.installed and not result.tracking_error:
        return
    if installing and result.tracking_error:
        print(
            f"Installation succeeded but ownership tracking failed: {result.tracking_error}",
            file=sys.stderr,
        )
    elif installing and not result.installed:
        print(f"Installation failed: {result.install_error or ''}", file=sys.stderr)
    elif result.winner:
        print(f"To install run: tink skill add {result.winner}")


def _print_route(result: RoutingResult, *, installing: bool) -> None:
    status = result.status
    noul = result.specialist_noul or 0.0
    if status in ("no_skill_needed", "no_match"):
        print(f"Status: {status} (specialist_noul: {noul:.2f}).")
        print("Standard coding tools and models are sufficient.")
    elif status == "no_candidates_available":
        print(f"Status: {status} (specialist_noul: {noul:.2f}).")
        print("No candidate skills available in the library.")
    elif status == "multi_routed":
        cands = result.candidates or []
        print(f"Recommended Skills ({len(cands)}):")
        for idx, cand in enumerate(cands, start=1):
            print(f"  {idx}. {cand['skill']} (p={cand['probability']:.2f})")
        if result.installed and not result.tracking_error:
            print(f"Installed winner: {result.skill_path}")
            print("Activation: Ready for immediate direct reading (no restart required).")
        else:
            _print_install_followup(result, installing)
    elif status == "routed":
        print(
            f"Recommended Skill: {result.winner} "
            f"(p={result.probability:.2f}, conf={result.confidence:.2f}, noul={noul:.2f})"
        )
        if result.installed and not result.tracking_error:
            print(f"Installed: {result.skill_path}")
            refs = result.references
            print(f"References: {', '.join(refs) if refs else '(none)'}")
            if result.scripts:
                print(f"Scripts: {', '.join(result.scripts)}")
            print("Activation: Ready for immediate direct reading (no restart required).")
        else:
            _print_install_followup(result, installing)
    else:
        top = result.top_candidate
        p = result.probability or 0.0
        threshold = result.threshold or 0.0
        if result.runner_up:
            rup_p = result.runner_up_probability or 0.0
            margin = result.margin or 0.0
            print(
                f"Status: uncertain. Top candidate '{top}' (p={p:.2f}) fell below threshold {threshold:.2f}. "
                f"Runner-up: '{result.runner_up}' (p={rup_p:.2f}, margin={margin:.2f})."
            )
        else:
            print(
                f"Status: uncertain. Top candidate '{top}' (p={p:.2f}) fell below threshold {threshold:.2f}."
            )


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    # Handle prune command (either `tink-route prune` or `tink-route --prune`)
    if args.task == "prune" or args.prune:
        try:
            res = _DEFAULT_ENGINE.prune(Path.cwd(), dry_run=args.dry_run, all_unpinned=args.all_unpinned)
        except Exception as e:
            err = {"error": str(e)}
            if args.json:
                print(json.dumps(err, indent=2))
            else:
                print(f"Prune error: {e}", file=sys.stderr)
            return 2

        if args.json:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            if res.dry_run:
                if res.pruned:
                    print(f"Eligible for pruning ({res.count}): {', '.join(res.pruned)}")
                else:
                    print("No transient skills eligible for pruning.")
            else:
                if res.pruned:
                    print(f"Pruned {res.count} ephemeral skill(s): {', '.join(res.pruned)}")
                    print("Clean state confirmed in .agents/skills/.")
                else:
                    print("No ephemeral skills to prune.")
                for err_item in res.errors:
                    print(f"Error removing {err_item['skill']}: {err_item['error']}", file=sys.stderr)
        if args.dry_run:
            return 0
        if res.errors:
            return 2
        return 0

    if args.task == "update":
        from .adapters.updater import UpdateError, check_for_update, perform_update

        try:
            check = check_for_update()
        except UpdateError as e:
            err = {"error": str(e)}
            if args.json:
                print(json.dumps(err))
            else:
                print(f"Update error: {e}", file=sys.stderr)
            return 2
        if not check.newer_available or check.asset is None:
            if args.json:
                print(json.dumps({"status": "up_to_date", "version": check.current}))
            else:
                print(f"Up to date (v{check.current}).")
            return 0
        if args.check:
            if args.json:
                print(json.dumps({"status": "update_available", "current": check.current, "latest": check.latest}))
            else:
                print(f"Update available: v{check.current} → v{check.latest} (re-run without --check to install).")
            return 1
        try:
            done = perform_update(check)
        except UpdateError as e:
            err = {"error": str(e)}
            if args.json:
                print(json.dumps(err))
            else:
                print(f"Update error: {e}", file=sys.stderr)
            return 2
        if args.json:
            print(json.dumps({"status": "updated", "previous": check.current, "version": done.current}))
        else:
            print(f"Updated v{check.current} → v{done.current}.")
        return 0

    if args.task == "version":
        if args.json:
            print(json.dumps({"version": __version__}))
        else:
            print(f"tink-route {__version__}")
        return 0

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

    if not args.library.is_dir():
        err = {"error": f"Skill library not found or not a directory: {args.library}"}
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
        ledger=default_ledger,
    )
    try:
        result = engine.route(
            task=args.task,
            skills=skills,
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

    install_failed = (
        result.status in ("routed", "multi_routed")
        and args.install
        and (not result.installed or result.tracking_error)
    )

    if args.json:
        print(json.dumps(result.to_dict(), indent=2))
    else:
        _print_route(result, installing=args.install)

    # Semantic exit code contract:
    # 0 = routed successfully (and installed if -i was passed)
    # 1 = unrouted (no skill needed or decision uncertain)
    # 2 = error (installation failed, API error, missing key/library)
    if install_failed:
        return 2
    return 0 if result.status in ("routed", "multi_routed") else 1


if __name__ == "__main__":
    sys.exit(main())
