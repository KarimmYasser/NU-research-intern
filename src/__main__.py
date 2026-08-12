"""CLI entry point: run the LangGraph pipeline (graph.run) from the command line.

Usage (from the repo root, so the cwd-relative Python install can find
site-packages - see /memories/repo/python-env.md):

    python -m src run \\
        --case-dir temp/out/rhvt \\
        --prompt "Describe the flow here..."

    # with an externally-provided mesh (patches read from its boundary file):
    python -m src run \\
        --mesh "temp/provided-mesh/3D RHVT (OpenFOAM Files)" \\
        --case-dir temp/out/rhvt \\
        --prompt "Describe the flow here..."

    # or read the prompt from a file:
    python -m src run --case-dir ... --prompt-file prompt.txt

Without --mesh the case uses box geometry (the LLM picks box_size/cells/patches)
and blockMesh generates the mesh during validation. Requires PYTHONPATH to
include src/ (see the sys.path shim below, matching tests/*.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1]
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from llm import LLMConfig  # noqa: E402
from orchestration import graph  # noqa: E402


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt_file:
        return Path(args.prompt_file).read_text().strip()
    if args.prompt:
        return args.prompt
    print("Enter the problem description, then Ctrl-Z/Ctrl-D + Enter:", file=sys.stderr)
    return sys.stdin.read().strip()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m src",
        description="Run the LangGraph OpenFOAM case-generation pipeline. "
                    "Without --mesh, a box-geometry case is rendered and meshed "
                    "by blockMesh; with --mesh, the case wraps that mesh.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run_p = sub.add_parser("run", help="Run the pipeline end-to-end.")
    run_p.add_argument("--mesh", default=None,
                        help="Optional path to a provided mesh (a polyMesh dir, "
                             "or a folder containing `boundary` directly). "
                             "Omit for box geometry meshed by blockMesh.")
    run_p.add_argument("--case-dir", required=True,
                        help="Where to write the rendered OpenFOAM case.")
    run_p.add_argument("--prompt", help="The natural-language problem statement.")
    run_p.add_argument("--prompt-file", help="Read the prompt from this file instead.")
    run_p.add_argument("--max-attempts", type=int, default=graph.DEFAULT_MAX_ATTEMPTS)
    run_p.add_argument("--model", help="Override LOCAL_LLM_MODEL for this run.")

    args = parser.parse_args(argv)

    if args.command == "run":
        nl = _read_prompt(args)
        if not nl:
            parser.error("no prompt given (use --prompt, --prompt-file, or stdin)")
        config = LLMConfig(model=args.model) if args.model else LLMConfig()
        final_state = graph.run(
            nl=nl,
            case_dir=Path(args.case_dir),
            mesh_dir=Path(args.mesh) if args.mesh else None,
            config=config,
            max_attempts=args.max_attempts,
        )
        result = final_state.get("validation")
        if result is not None:
            print(f"\n{'=' * 60}\nvalidation stage: {result.stage}\nok: {result.ok}")
            if not result.ok:
                for err in result.errors:
                    print(f"  [{err.failure_class.name}] {err.file_hint or ''}: {err.message}")
            is_ok = result.ok
        else:
            is_ok = False
        attempt = final_state.get("attempt", 0)
        print(f"attempts used: {attempt}/{args.max_attempts}")
        print(f"case written to: {args.case_dir}")
        return 0 if is_ok else 1

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
