"""Run the offline DOM integration with the real local HTTP control plane."""

import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
origin = f"http://127.0.0.1:{port}"
with tempfile.TemporaryDirectory() as temp:
    env = {
        **os.environ,
        "INITIALIZER_MODE": "mock",
        "INITIALIZER_ORIGIN": origin,
        "INITIALIZER_DATABASE": str(Path(temp) / "mock.sqlite3"),
        "TEST_ORIGIN": origin,
    }
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "initializer.app:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--no-access-log",
        ],
        cwd=ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        for _ in range(80):
            try:
                if httpx.get(origin + "/api/session", trust_env=False).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Mock server failed to start")
        subprocess.run(
            ["node", "tests/initializer_dom.cjs"], cwd=ROOT, env=env, check=True, timeout=120
        )
    finally:
        proc.terminate()
        proc.wait(timeout=10)
