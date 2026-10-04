#!/usr/bin/env python3
"""Render a Fly configuration locally. Never signs in or calls a cloud API."""

from __future__ import annotations

import argparse
import re
import tomllib
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[1] / "fly.example.toml"


def render_config(app: str, region: str) -> str:
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", app):
        raise ValueError("App name must be 1–63 lowercase letters, digits or internal hyphens")
    if not re.fullmatch(r"[a-z]{3}", region):
        raise ValueError("Region must be a three-letter Fly region code")
    result = TEMPLATE.read_text().replace("__APP_NAME__", app).replace("__REGION__", region)
    tomllib.loads(result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True, help="App name; availability is not checked")
    parser.add_argument("--region", required=True, help="Region code; availability is not checked")
    parser.add_argument("--output", type=Path, default=Path("fly.toml"))
    args = parser.parse_args(argv)
    try:
        rendered = render_config(args.app, args.region)
        with args.output.open("x") as stream:
            stream.write(rendered)
    except (OSError, ValueError) as exc:
        # Do not echo arbitrary paths, input values, or exception payloads.
        parser.exit(2, f"Could not write configuration ({type(exc).__name__}); "
                       "check app/region format and use a new writable output path.\n")
    print("Wrote local Fly configuration. No account, resources or deployment were accessed.")
    print("Review docs/fly-deployment.md and approve resources before deploying.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
