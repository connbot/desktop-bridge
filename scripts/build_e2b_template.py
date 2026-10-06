#!/usr/bin/env python3
"""Build a cold E2B template from a previously published, digest-pinned desktop image."""

from __future__ import annotations

import argparse
import os
import re


def make_template(image: str):
    if not re.fullmatch(r"[a-zA-Z0-9./:_-]+@sha256:[a-f0-9]{64}", image):
        raise ValueError("Use a registry image pinned by its sha256 digest")
    from e2b import Template

    # Do not translate our multistage Dockerfile: build the e2b target with Docker
    # first, then import its complete image. E2B ignores image CMD/ENTRYPOINT.
    # The start command runs at BUILD time and is snapshotted, not rerun at create.
    return (Template().from_image(image).set_user("root").set_workdir("/app")
            .set_envs({"HOME": "/root", "PATH": "/usr/sbin:/usr/bin:/sbin:/bin",
                       "BASH_ENV": "/dev/null", "ENV": "/dev/null"})
            .set_start_cmd("sleep infinity",
                "/usr/bin/python3 -I -S /opt/agent-computer/e2b-entrypoint.py --prepare-template"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True, help="Public registry image@sha256:DIGEST")
    parser.add_argument("--alias", required=True, help="Template alias in your E2B account")
    args = parser.parse_args(argv)
    if not os.environ.get("E2B_API_KEY"):
        parser.error("Set E2B_API_KEY in your trusted terminal first")
    try:
        from e2b import Template

        template = make_template(args.image)
        result = Template.build(template, name=args.alias, cpu_count=2, memory_mb=4096)
        print("Template built: " + result.template_id)
        print("No desktop, owner token, browser profile or OAuth grant was started in this snapshot.")
    except Exception as exc:
        parser.exit(1, f"Template build failed ({type(exc).__name__}); check E2B build status.\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
