#!/usr/bin/env python3
"""Owner-run local setup. Never uploads or prints the generated token."""

import os
import secrets
from pathlib import Path

path = Path(".env")
if path.exists():
    print(".env already exists; keeping your existing configuration.")
else:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write("BRIDGE_OWNER_TOKEN=" + secrets.token_urlsafe(40) + "\n")
        stream.write("BRIDGE_PUBLIC_URL=http://localhost:8080\n")
    print("Created private .env. Use its owner token to sign in locally.")
print("Next: docker compose up --build -d")
print("Open http://localhost:8080. Do not publish internal VNC or CDP ports.")
