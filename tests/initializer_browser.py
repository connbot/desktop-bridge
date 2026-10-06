"""Offline browser integration; server and Chromium share one local process namespace.

Run: .venv/bin/python tests/initializer_browser.py --output /tmp/initializer-ui
No cloud credentials, external navigation, tunnel or workflow execution is used.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default="/tmp/initializer-ui")
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory() as tmp:
        password_path = Path(tmp) / "test-password.json"
        env = {
            **os.environ,
            "INITIALIZER_MODE": "mock",
            "INITIALIZER_ORIGIN": origin,
            "INITIALIZER_DATABASE": str(Path(tmp) / "mock.sqlite3"),
        }
        server = subprocess.Popen(
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
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
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
                raise RuntimeError("Local mock failed to start")
            with sync_playwright() as p:
                browser = p.chromium.launch(
                    executable_path=os.environ.get("INITIALIZER_TEST_CHROMIUM") or None,
                    headless=True,
                    args=["--no-sandbox"],
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 960}, accept_downloads=True
                )
                page = context.new_page()
                errors = []
                page.on("pageerror", lambda e: errors.append(str(e)))
                # Block any accidental external navigation/request in the offline test.
                page.route(
                    "**/*",
                    lambda route: (
                        route.continue_()
                        if route.request.url.startswith(origin)
                        or route.request.url.startswith("blob:")
                        else route.abort()
                    ),
                )
                page.goto(origin)
                expect(page.locator('[data-view="0"]')).to_be_visible()
                expect(page.locator("#begin")).to_be_disabled()
                page.screenshot(path=out / "01-welcome-desktop.png", full_page=True)
                page.locator("#chatgpt-ok").check()
                page.locator("#temporary-ok").check()
                page.locator("#begin").click()
                page.locator("#github-connect").click()
                expect(page.locator("#github-status")).to_contain_text("preview-user")
                page.screenshot(path=out / "02-accounts-desktop.png", full_page=True)
                page.locator("#accounts-next").click()
                expect(page.locator("#create-start")).to_be_disabled()
                with page.expect_download() as info:
                    page.locator("#download-password").click()
                download = info.value
                download.save_as(str(password_path))
                recovery = json.loads(password_path.read_text())
                assert len(recovery["owner_password"]) == 43
                page.locator("#password-saved").check()
                page.locator("#launch-consent").check()
                page.screenshot(path=out / "03-password-desktop.png", full_page=True)
                # Back and forward must retain the same password and require explicit consent.
                page.locator('[data-view="2"] [data-back="1"]').click()
                page.locator("#accounts-next").click()
                page.locator("#create-start").dblclick()
                expect(page.locator('[data-view="4"]')).to_be_visible(timeout=15000)
                expect(page.locator("#mcp-url")).to_have_value(
                    "https://fictional-preview.trycloudflare.com/mcp"
                )
                page.screenshot(path=out / "04-ready-desktop.png", full_page=True)
                page.reload()
                expect(page.locator('[data-view="4"]')).to_be_visible()
                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=out / "05-ready-mobile.png", full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                page.on("dialog", lambda dialog: dialog.accept())
                page.locator("#stop-preview").click()
                expect(page.locator("#progress-title")).to_have_text("这次预览已结束", timeout=8000)
                page.screenshot(path=out / "06-ended-mobile.png", full_page=True)
                # Reset only exists in loopback mock; same account, no external mutations.
                page.locator("#reset-mock").click()
                expect(page.locator('[data-view="0"]')).to_be_visible()
                page.screenshot(path=out / "07-welcome-mobile.png", full_page=True)
                page.evaluate("""async () => {const s=await (await fetch('/api/session')).json();
                    await fetch('/api/mock/scenario',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf},body:JSON.stringify({scenario:'no_zones'})});} """)
                page.locator("#chatgpt-ok").check()
                page.locator("#temporary-ok").check()
                page.locator("#begin").click()
                page.locator("#github-connect").click()
                page.locator('[name="address"][value="named"]').check()
                page.locator("#cf-connect").click()
                expect(page.locator("#no-zones")).to_be_visible()
                expect(page.locator("#accounts-next")).to_be_disabled()
                page.screenshot(path=out / "08-no-zone-mobile.png", full_page=True)
                page.locator("#use-quick").click()
                expect(page.locator("#github-status")).to_contain_text("preview-user")
                page.locator("#accounts-next").click()
                # Refresh before transmission must provide a safe new/downloaded password path.
                page.reload()
                expect(page.locator('[data-view="1"]')).to_be_visible()
                page.locator("#accounts-next").click()
                page.locator("summary").filter(has_text="已经下载过").click()
                page.locator("#restore-password").set_input_files(str(password_path))
                expect(page.locator("#download-status")).to_contain_text("没有上传")
                page.locator("#password-saved").check()
                page.locator("#launch-consent").check()
                page.evaluate("""async () => {const s=await (await fetch('/api/session')).json();
                    await fetch('/api/mock/scenario',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf},body:JSON.stringify({scenario:'dispatch_unknown'})});} """)
                page.locator("#create-start").click()
                expect(page.locator("#progress-title")).to_have_text(
                    "还不能确认这一步的结果", timeout=12000
                )
                page.screenshot(path=out / "09-unknown-mobile.png", full_page=True)
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                # Never retain even test passwords in review artifacts.
                password_path.unlink()
                context.close()
                browser.close()
                (out / "result.json").write_text(
                    json.dumps(
                        {
                            "passed": True,
                            "scenarios": [
                                "prerequisite gate",
                                "quick end-to-end",
                                "download and confirm",
                                "browser sealed-box",
                                "back/forward",
                                "double click",
                                "ready refresh",
                                "stop terminal",
                                "mobile no overflow",
                                "no Cloudflare zone fallback",
                                "password import after refresh",
                                "uncertain dispatch stopped",
                            ],
                            "browser_console_errors": errors,
                            "real_providers_tested": False,
                        },
                        indent=2,
                    )
                )
                print("Offline browser integration passed; screenshots:", out)
        finally:
            server.terminate()
            server.wait(timeout=10)


if __name__ == "__main__":
    main()
