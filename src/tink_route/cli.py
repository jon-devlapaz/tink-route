import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

from .client import DEFAULT_MODEL, DEFAULT_THRESHOLD, JevRouterClient
from .metadata import load_library_skills

DEFAULT_LIBRARY_PATH = Path.home() / ".tink" / "skills"


def install_skill(skill_name: str) -> Dict[str, Any]:
    """Execute `tink skill add <skill_name>` to install skill into project."""
    res = subprocess.run(
        ["tink", "skill", "add", skill_name],
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "success": res.returncode == 0,
        "stdout": res.stdout.strip(),
        "stderr": res.stderr.strip(),
        "code": res.returncode,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tink-route",
        description="Dynamic Agent Skill Router using TypeSafe Jev.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("task", help="The user's task or query description to evaluate.")
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

    api_key = os.environ.get("TYPESAFE_API_KEY")
    if not api_key:
        err = {"error": "TYPESAFE_API_KEY environment variable is not set."}
        if args.json:
            print(json.dumps(err))
        else:
            print(f"Error: {err['error']}", file=sys.stderr)
        return 2

    skills = load_library_skills(args.library)
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
    if result["status"] == "routed" and args.install:
        install_res = install_skill(result["winner"])
        result["installed"] = install_res["success"]
        result["install_output"] = install_res["stdout"] or install_res["stderr"]
        if not install_res["success"]:
            result["install_error"] = install_res["stderr"]

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
            if result.get("installed"):
                print(f"Installed into .agents/skills/{winner}/ ({result.get('install_output', '')})")
            elif args.install and not result.get("installed"):
                print(f"Installation failed: {result.get('install_error', '')}", file=sys.stderr)
            else:
                print(f"To install run: tink skill add {winner}")
        else:
            print(f"Status: {status}. No single skill clearly exceeded the {result['threshold']:.2f} threshold.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
