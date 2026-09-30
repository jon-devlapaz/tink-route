"""Command line interface for tink-route: parse arguments, then hand off to flow."""

import argparse
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .adapters.client import DEFAULT_MODEL, DEFAULT_THRESHOLD
from .adapters.executor import DefaultSubprocessExecutor
from .core.constants import FITS_THRESHOLD, get_default_library_path
from .flow import deliver, pick


class _TinkRouteParser(argparse.ArgumentParser):
    """Usage errors print two short stderr lines instead of the full usage dump."""

    def error(self, message: str):  # type: ignore[override]
        self.exit(2, f"tink-route: error: {message}\nTry: tink-route --help\n")


_USAGE = 'tink-route [--skillset NAME | --anywhere] [--pick] [--json] [--receipt PATH] "<task>"'

_EPILOG = (
    "For agents, add one line to AGENTS.md:\n"
    "  When a task needs a specialised procedure you do not already know, run:\n"
    '  tink-route "<what you need>" and follow the output; if it exits non-zero, continue without it.\n'
    "\n"
    "It searches the whole skill library. --skillset NAME restricts it to that skillset's shelf; if the\n"
    "shelf has no skill, a Hint line may name one on another shelf, which is never delivered.\n"
    "\n"
    "Exit codes: 0 delivered (--pick: routed), 1 no skill applies, 2 could not deliver or usage error."
)


def build_parser() -> argparse.ArgumentParser:
    parser = _TinkRouteParser(
        prog="tink-route",
        usage=_USAGE,
        description="Route a task to one specialist skill, verify it with `tink mount`, and print it.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        add_help=False,
        allow_abbrev=False,
        epilog=_EPILOG,
    )
    opts = parser.add_argument_group("Options")
    adv = parser.add_argument_group("Advanced")

    opts.add_argument("task", nargs="?", help="What you need done; the skill is chosen for this.")
    opts.add_argument(
        "--skillset", metavar="NAME",
        help="Restrict the search to this skillset (minus its `required` skills), with an off-shelf hint.",
    )
    opts.add_argument("--anywhere", action="store_true",
                      help="Search the whole library (the default; accepted so callers can say so).")
    opts.add_argument(
        "--pick", action="store_true",
        help="Only decide: print the chosen skill, mount nothing, write nothing.",
    )
    opts.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    opts.add_argument(
        "--receipt", type=Path, default=None, metavar="PATH",
        help="Append one JSON line per delivery to PATH (env: TINK_ROUTE_RECEIPT).",
    )
    opts.add_argument("-h", "--help", action="help", help="Show this help and exit.")
    opts.add_argument("--version", action="version", version=f"%(prog)s {__version__}",
                      help="Show the version and exit.")

    adv.add_argument("--library", type=Path, default=None, metavar="DIR",
                     help="Skill library (default: $TINK_HOME/skills or ~/.tink-library/skills).")
    adv.add_argument("--model", default=DEFAULT_MODEL, metavar="NAME", help=f"Routing model (default: {DEFAULT_MODEL}).")
    adv.add_argument("--threshold", type=float, metavar="P", default=DEFAULT_THRESHOLD,
                     help=f"Minimum confidence to accept a skill (default: {DEFAULT_THRESHOLD}).")
    adv.add_argument("--tri-gate", action=argparse.BooleanOptionalAction, default=True,
                     help="Ask the specialised-workflow gate first (default: on).")
    adv.add_argument("--rerank", action=argparse.BooleanOptionalAction, default=True,
                     help="Rerank the top candidates against SKILL.md excerpts (default: on).")
    adv.add_argument("--fits-threshold", type=float, metavar="P", default=FITS_THRESHOLD,
                     help=f"Minimum per-skill fit to accept a reranked winner (default: {FITS_THRESHOLD}).")
    adv.add_argument("--deadline", type=float, default=None, metavar="SECONDS",
                     help="Per-API-call timeout; disables retries.")
    adv.add_argument("--inline-max", type=int, default=12000, metavar="N",
                     help="Print skills up to N chars inline; mount larger ones and give the path "
                     "(default: 12000).")
    return parser


def main(argv: Optional[list[str]] = None, *, route_fn=None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        if not args.task or not args.task.strip():
            parser.error('a task is required: tink-route "<what you need>".')
        if args.anywhere and args.skillset:
            parser.error("--anywhere and --skillset cannot be used together.")
        if args.inline_max < 0:
            parser.error("--inline-max must be >= 0.")
        if args.pick and args.receipt:
            parser.error("--pick writes nothing, so --receipt does not apply.")
    except SystemExit as exc:  # argparse exits for usage errors, --help and --version
        return exc.code if isinstance(exc.code, int) else 0

    args.library = Path(args.library) if args.library else get_default_library_path()
    if args.pick:
        return pick(args, route_fn=route_fn)
    return deliver(args, route_fn=route_fn, executor=DefaultSubprocessExecutor())


if __name__ == "__main__":
    sys.exit(main())
