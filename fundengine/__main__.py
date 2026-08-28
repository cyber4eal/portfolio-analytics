"""python3 -m fundengine build [--agent-dir PATH] [--allocation 0.10]"""

from __future__ import annotations

import argparse
import sys

from . import publish

DEFAULT_AGENT_DIR = (
    "/Users/catalin_main/Library/Application Support/Claude/"
    "local-agent-mode-sessions/b6bac09d-05f2-42ba-bfcf-deef8d796d7d/"
    "aea9c31f-2d00-462a-a14b-1a723c07a247/"
    "local_143c8db9-8af1-4880-98e5-77b3e0d4f58a/outputs/portfolio-agent"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fundengine")
    parser.add_argument("command", choices=["build"])
    parser.add_argument("--agent-dir", default=DEFAULT_AGENT_DIR,
                        help="portfolio-agent checkout holding .env and secrets/")
    parser.add_argument("--allocation", type=float, default=0.10,
                        help="trial weight when ranking funds as additions")
    args = parser.parse_args(argv)

    payload = publish.build(args.agent_dir, allocation=args.allocation)
    publish.write(payload)
    print(f"Done, as at {payload['asOf']}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
