"""Opt-in web initializer. Run one worker; real provider activation is operator controlled."""

import asyncio
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import Config
from .network import accepted_origin, probe
from .providers import ProviderError, Providers, oauth_challenge, sealed, valid_ciphertext
from .store import Store

STATIC = Path(__file__).parent / "static"
TERMINAL = {"ended", "failed", "unknown", "interrupted"}
ERRORS = {
    "not_configured": "服务端还没有配置 GitHub App。当前不能进行真实初始化，请联系站点维护者。",
    "cloudflare_not_configured": "服务端还没有配置 Cloudflare 授权。可以先选择临时地址。",
    "template_not_enabled": "维护者尚未将源仓库设为模板。自动创建已停止，没有改用更广的 GitHub 权限。",
    "template_version_changed": "模板版本发生变化，需要维护者审核新版本后再开放初始化。",
    "repository_version_changed": "仓库内容与已审核模板不一致，已停止写入或启动。",
    "repository_name_taken": "专用仓库名已存在。我们没有覆盖它；请联系维护者检查这次初始化。",
    "permission_denied": "GitHub 或 Cloudflare 拒绝了这一步。请检查授权、组织策略和 GitHub Actions 额度。",
    "authorization_expired": "账号授权已过期或撤销。请重新连接同一个账号后检查当前进度。",
    "installation_required": "请先安装 GitHub App，再回来点击“检查安装”。只选择本项目需要的仓库。",
    "installation_permissions_missing": "GitHub App 缺少这一步需要的权限，请由维护者检查配置。",
    "personal_account_required": "这个版本支持个人 GitHub 账号；组织账号暂未开放。",
    "actions_disabled": "这个仓库的 Actions 不可用。请在 GitHub 检查 Actions 设置或账号策略，再检查进度。",
    "provider_unavailable": "服务暂时没有回应。若某一步可能已创建资源，我们会停止，避免重复创建。",
    "dispatch_result_unknown": "GitHub 可能已收到启动请求，但没有返回运行编号。请先到仓库 Actions 检查，不要重复启动。",
    "rate_limited": "请求太频繁，请稍后重试。",
    "tunnel_binding_changed": "固定地址的 DNS 或 Tunnel 路由已变化。已停止启动，避免把密码发到错误地址。请恢复本次资源的正确绑定。",
    "zone_unavailable": "这个域名还没有在你的 Cloudflare 账号中生效。可以先改用临时地址。",
    "hostname_taken": "这个子域名已有记录。我们没有覆盖它。",
    "readiness_pending": "电脑已启动，但公网连接检查还未通过，请稍候。",
    "run_failed": "这次运行已经结束或失败。请打开 GitHub 运行记录查看原因；桌面文件不会保留。",
    "session_expired": "这次网页会话已过期，请重新连接 GitHub。已创建的资源不会因此自动删除。",
    "interrupted": "初始化服务曾重启。为避免重复创建，已暂停这次操作；请按资源清单检查后处理。",
    "password_required": "请下载密码文件，并确认能够打开后再启动。",
    "invalid_request": "请求不完整或已经过期。请刷新页面并检查当前步骤。",
}


def create_app(config=None, providers=None, readiness_probe=None):
    c = config or Config.from_env()
    c.validate()
    p = providers
    if p is None:
        if c.mode == "mock":
            from .mock import MockProviders

            p = MockProviders()
        else:
            p = Providers(c)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    db = Store(c.database)
    sessions, tasks, locks, owner_locks = {}, {}, {}, {}
    session_rates = {}
    cookie = "bridge_initializer_mock" if c.mode == "mock" else "__Host-bridge_initializer"
    if c.mode == "live" and not c.github_ready:
        cookie = "bridge_initializer_unconfigured"
    for op in db.all():
        if op["stage"] not in TERMINAL:
            op.update(stage="interrupted", error="interrupted")
            db.save(op)
    app.state.config, app.state.providers, app.state.store = c, p, db
    app.state.sessions = sessions
    check_ready = readiness_probe or probe

    def session(request, required=True):
        sid = request.cookies.get(cookie)
        s = sessions.get(sid)
        if s and s["expires"] > time.time():
            return s
        if sid:
            sessions.pop(sid, None)
        if required:
            raise ProviderError("session_expired")
        return None

    def require_user(request):
        s = session(request)
        if not s.get("user") or not s.get("installation_id"):
            raise ProviderError("authorization_expired")
        return s

    def owned(request, op_id):
        s = require_user(request)
        op = db.get(op_id, s["user"]["id"])
        if not op:
            raise ProviderError("invalid_request")
        return s, op

    def public_op(op):
        if not op:
            return None
        data = {k: v for k, v in op.items() if k not in {"owner_id", "installation_id", "scenario"}}
        data["message"] = (
            ERRORS.get(op.get("error"), "这一步需要检查，请查看资源清单和运行记录。")
            if op.get("error")
            else ""
        )
        data["mock"] = c.mode == "mock"
        return data

    def save(op, stage=None):
        if stage:
            op["stage"] = stage
        db.save(op)

    def fail(op, e):
        op.update(error=e.code, uncertain=e.uncertain, failed_stage=op["stage"])
        save(op, "unknown" if e.uncertain else "failed")

    async def prepare(op, s):
        try:
            if not op.get("repo_id"):
                save(op, "creating_repository")
                op.update(await p.create_repository(op, s["github_token"]))
            save(op, "checking_repository")
            token = await p.repository_token(op)
            # Template generation is asynchronous; read-only checks may be retried.
            for attempt in range(12):
                try:
                    op["commit"] = await p.verify_repository(op, token)
                    break
                except ProviderError as e:
                    if e.code != "not_found" or attempt == 11:
                        raise
                    await asyncio.sleep(2)
            key = await p.public_key(op, token)
            op.update(key_id=key["key_id"], public_key=key["key"])
            if op["mode"] == "named":
                save(op, "creating_address")
                if not op.get("tunnel_id"):
                    await p.check_hostname(op, s["cloudflare_token"])
                    tunnel = await p.create_tunnel(op, s["cloudflare_token"])
                    op["tunnel_id"] = tunnel["id"]
                    save(op)
                if not op.get("dns_id"):
                    op["dns_id"] = await p.configure_tunnel(op, s["cloudflare_token"])
                    save(op)
                if not op.get("cf_token_written"):
                    connector = await p.tunnel_token(op, s["cloudflare_token"])
                    await p.write_secret(
                        op,
                        token,
                        "CLOUDFLARE_TUNNEL_TOKEN",
                        sealed(connector, key["key"]),
                        key["key_id"],
                    )
                    connector = None
                    op["cf_token_written"] = True
            op["error"] = ""
            save(op, "awaiting_password")
        except ProviderError as e:
            fail(op, e)
        except Exception:
            # Never expose raw provider bodies, exception strings, or credentials.
            fail(op, ProviderError("provider_unavailable", uncertain=True))

    async def launch(op, encrypted, s):
        try:
            token = await p.repository_token(op)
            commit = await p.verify_repository(op, token)
            if commit != op["commit"]:
                raise ProviderError("repository_version_changed")
            if op["mode"] == "named":
                if not s.get("cloudflare_token"):
                    raise ProviderError("authorization_expired")
                await p.verify_tunnel(op, s["cloudflare_token"])
            if encrypted is not None:
                save(op, "saving_password")
                await p.write_secret(op, token, "BRIDGE_OWNER_TOKEN", encrypted, op["key_id"])
                op["secret_written"] = True
            elif not op.get("secret_written"):
                raise ProviderError("password_required")
            op["dispatch_at"] = time.time()
            op["dispatch_nonce"] = secrets.token_hex(16)
            save(op, "launching")
            op.update(await p.dispatch(op, token))
            save(op, "waiting_ready")
            if c.mode == "mock":
                await asyncio.sleep(0.8)
                if p.scenario == "readiness_failed":
                    op["error"] = "readiness_pending"
                    save(op)
                else:
                    current = db.get(op["id"])
                    if current["stage"] != "waiting_ready":
                        return
                    op = current
                    op.update(
                        origin="https://"
                        + (
                            op.get("hostname")
                            or (
                                "fictional-preview.trycloudflare.com"
                                if op["run_id"] == 987
                                else f"fictional-preview-{op['run_id']}.trycloudflare.com"
                            )
                        ),
                        expires_at=time.time() + 3600,
                        last_seen=time.time(),
                    )
                    save(op, "ready")
        except ProviderError as e:
            fail(op, e)
        except Exception:
            fail(op, ProviderError("provider_unavailable", uncertain=True))

    @app.exception_handler(ProviderError)
    async def error_handler(request, e):
        status = 401 if e.code in {"session_expired", "authorization_expired"} else 409
        return JSONResponse(
            {
                "code": e.code,
                "message": ERRORS.get(e.code, ERRORS["invalid_request"]),
                "uncertain": e.uncertain,
            },
            status_code=status,
        )

    @app.middleware("http")
    async def boundaries(request, call_next):
        if request.headers.get("host") != c.origin.split("://", 1)[1]:
            return JSONResponse({"message": "Unrecognized host"}, status_code=400)
        if (
            c.mode == "mock"
            and request.client
            and request.client.host not in {"127.0.0.1", "::1", "testclient"}
        ):
            return JSONResponse({"message": "Mock mode is loopback only"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and not request.url.path.startswith(
            "/api/run-events/"
        ):
            s = session(request, required=False)
            if (
                request.headers.get("origin") != c.origin
                or not s
                or not secrets.compare_digest(request.headers.get("x-csrf-token", ""), s["csrf"])
            ):
                return JSONResponse({"message": "页面已过期，请刷新后重试。"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            chunks, size = [], 0
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16384:
                    return JSONResponse({"message": "Request too large"}, status_code=413)
                chunks.append(chunk)
            request._body = b"".join(chunks) or b"{}"
            try:
                if not isinstance(json.loads(request._body), dict):
                    raise ValueError()
            except (ValueError, UnicodeDecodeError):
                return JSONResponse({"message": "Invalid JSON object"}, status_code=400)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "Pragma": "no-cache",
                "Referrer-Policy": "no-referrer",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
                "Content-Security-Policy": "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        if c.origin.startswith("https://"):
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        return response

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html")

    @app.get("/api/session")
    async def get_session(request: Request):
        s = session(request, required=False)
        new = not s
        if new:
            ip = request.client.host if request.client else "unknown"
            now = time.time()
            for key, values in list(session_rates.items()):
                if values[-1] < now - 60:
                    session_rates.pop(key, None)
            attempts = [t for t in session_rates.get(ip, []) if t > now - 60]
            if len(attempts) >= 20 or len(session_rates) >= 1000:
                raise ProviderError("rate_limited")
            session_rates[ip] = attempts + [now]
            sid = secrets.token_urlsafe(32)
            s = {"csrf": secrets.token_urlsafe(32), "expires": time.time() + c.session_seconds}
            # Bound abandoned unauthenticated sessions.
            for key, value in list(sessions.items()):
                if value["expires"] <= time.time():
                    sessions.pop(key, None)
            if len(sessions) >= 1000:
                raise ProviderError("rate_limited")
            sessions[sid] = s
        result = {
            "csrf": s["csrf"],
            "mode": c.mode,
            "github_ready": c.github_ready or c.mode == "mock",
            "cloudflare_ready": c.cloudflare_ready or c.mode == "mock",
            "user": s.get("user"),
            "installed": bool(s.get("installation_id")),
            "zones": s.get("zones", []),
            "cloudflare_connected": bool(s.get("cloudflare_token")),
            "notice": s.pop("notice", None),
            "operation": public_op(db.latest(s["user"]["id"])) if s.get("user") else None,
        }
        response = JSONResponse(result)
        if new:
            response.set_cookie(
                cookie,
                sid,
                httponly=True,
                secure=c.origin.startswith("https://"),
                samesite="lax",
                max_age=c.session_seconds,
                path="/",
            )
        return response

    @app.post("/api/auth/{provider}")
    async def begin_auth(provider: str, request: Request):
        s = session(request)
        if provider not in {"github", "cloudflare"}:
            raise ProviderError("invalid_request")
        if provider == "cloudflare":
            require_user(request)
        if c.mode == "mock":
            if provider == "github":
                s.update(user=await p.identity("mock"), github_token="mock", installation_id=101)
            else:
                s.update(cloudflare_token="mock", zones=await p.zones("mock"))
            return {"redirect": "/"}
        if not (c.github_ready if provider == "github" else c.cloudflare_ready):
            raise ProviderError(
                "not_configured" if provider == "github" else "cloudflare_not_configured"
            )
        state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(48)
        s["oauth"] = {
            "provider": provider,
            "state": state,
            "verifier": verifier,
            "expires": time.time() + 600,
        }
        params = {
            "client_id": c.github_client_id if provider == "github" else c.cf_client_id,
            "redirect_uri": c.origin + f"/auth/{provider}/callback",
            "state": state,
            "response_type": "code",
            "code_challenge": oauth_challenge(verifier),
            "code_challenge_method": "S256",
        }
        if provider == "cloudflare":
            params["scope"] = c.cf_scopes
        base = (
            "https://github.com/login/oauth/authorize"
            if provider == "github"
            else "https://dash.cloudflare.com/oauth2/auth"
        )
        return {"redirect": base + "?" + urlencode(params)}

    @app.get("/auth/{provider}/callback")
    async def callback(provider: str, request: Request):
        s = session(request)
        pending = s.pop("oauth", None)
        state = request.query_params.get("state", "")
        if (
            not pending
            or pending["provider"] != provider
            or pending["expires"] <= time.time()
            or not secrets.compare_digest(state, pending["state"])
        ):
            raise ProviderError("invalid_request")
        if request.query_params.get("error") or not request.query_params.get("code"):
            s["notice"] = "授权已取消。尚未创建新资源，你可以继续或选择临时地址。"
            return RedirectResponse("/", status_code=303)
        try:
            token = await p.exchange(provider, request.query_params["code"], pending["verifier"])
            if provider == "github":
                user = await p.identity(token)
                if s.get("user") and s["user"]["id"] != user["id"]:
                    raise ProviderError("invalid_request")
                s.update(user=user, github_token=token)
                try:
                    s["installation_id"] = await p.installation(token, user)
                except ProviderError as e:
                    if e.code != "installation_required":
                        raise
            else:
                if not s.get("user"):
                    raise ProviderError("authorization_expired")
                s.update(cloudflare_token=token, zones=await p.zones(token))
        except ProviderError as e:
            s["notice"] = ERRORS.get(e.code, ERRORS["invalid_request"])
        # Rotate session identifier after provider authentication, preserving state only server-side.
        old = request.cookies.get(cookie)
        sessions.pop(old, None)
        sid = secrets.token_urlsafe(32)
        s["csrf"] = secrets.token_urlsafe(32)
        s["expires"] = time.time() + c.session_seconds
        sessions[sid] = s
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            cookie,
            sid,
            httponly=True,
            secure=True,
            samesite="lax",
            path="/",
            max_age=c.session_seconds,
        )
        return response

    @app.post("/api/installation")
    async def installation(request: Request):
        s = session(request)
        if not s.get("github_token"):
            raise ProviderError("authorization_expired")
        s["installation_id"] = await p.installation(s["github_token"], s["user"])
        return {"installed": True}

    @app.get("/api/install-link")
    async def install_link(request: Request):
        s = session(request)
        if not s.get("user") or not c.github_ready:
            raise ProviderError("not_configured")
        return {"url": f"https://github.com/apps/{c.github_slug}/installations/new"}

    @app.post("/api/operations")
    async def create_operation(request: Request):
        s = require_user(request)
        async with owner_locks.setdefault(str(s["user"]["id"]), asyncio.Lock()):
            body = await request.json()
            if body.get("saved_password") is not True or body.get("accepted_preview") is not True:
                raise ProviderError("password_required")
            previous = db.latest(s["user"]["id"])
            if previous:
                # One installation per owner in this MVP. Never silently make another after a failure.
                return public_op(previous)
            mode = body.get("mode")
            if mode not in {"quick", "named"}:
                raise ProviderError("invalid_request")
            op_id = secrets.token_hex(16)
            op = {
                "id": op_id,
                "owner_id": s["user"]["id"],
                "owner_login": s["user"]["login"],
                "installation_id": s["installation_id"],
                "repo_name": "desktop-bridge-" + op_id[:12],
                "mode": mode,
                "stage": "queued",
                "created_at": time.time(),
                "last_sequence": 0,
            }
            if mode == "named":
                if not s.get("cloudflare_token"):
                    raise ProviderError("authorization_expired")
                zones = await p.zones(s["cloudflare_token"])
                zone = next((z for z in zones if z["id"] == body.get("zone_id")), None)
                if not zone:
                    raise ProviderError("zone_unavailable")
                op.update(
                    zone_id=zone["id"],
                    zone_name=zone["name"],
                    account_id=zone["account_id"],
                    hostname="desktop-" + op_id[:12] + "." + zone["name"],
                )
            save(op)
            tasks[op_id] = asyncio.create_task(prepare(op, s))
            return public_op(op)

    @app.get("/api/operations/{op_id}")
    async def operation(op_id: str, request: Request):
        owned(request, op_id)  # Reject unknown/cross-tenant IDs before allocating locks.
        async with locks.setdefault(op_id, asyncio.Lock()):
            s, op = owned(request, op_id)
            if (
                op["stage"] == "unknown"
                and op.get("failed_stage") == "launching"
                and op.get("repo_id")
                and not op.get("run_id")
            ):
                try:
                    token = await p.repository_token(op)
                    found = await p.find_dispatched_run(op, token)
                    if found:
                        op.update(found)
                        op.update(error="", uncertain=False)
                        save(op, "waiting_ready")
                except ProviderError:
                    pass  # An uncertain write must stay blocked until a unique run is found.
            if op["stage"] == "ready" and op.get("last_seen", 0) < time.time() - 90:
                op.pop("origin", None)
                op["error"] = "readiness_pending"
                save(op, "checking_status")
            # GET reconciles state only; no external mutations, no opportunistic retries.
            if op.get("run_id") and op["stage"] in {
                "waiting_ready",
                "ready",
                "stopping",
                "interrupted",
                "ending",
                "checking_status",
            }:
                try:
                    token = await p.repository_token(op)
                    run = await p.get_run(op, token)
                    if c.mode == "mock" and op["stage"] == "ready":
                        op["last_seen"] = time.time()
                        save(op)
                    if run.get("status") == "completed":
                        op.pop("origin", None)
                        op["error"] = (
                            "run_failed"
                            if run.get("conclusion") not in {"success", "cancelled"}
                            else ""
                        )
                        save(op, "ended")
                    elif op.get("expires_at", float("inf")) <= time.time():
                        op.pop("origin", None)
                        save(op, "ending")
                    elif op["stage"] in {"interrupted", "checking_status"}:
                        save(op, "waiting_ready")
                except ProviderError as e:
                    op["error"] = e.code
                    # Failed reconciliation must not continue to claim online.
                    if op["stage"] == "ready":
                        save(op, "checking_status")
            return public_op(op)

    @app.post("/api/operations/{op_id}/start")
    async def start(op_id: str, request: Request):
        s, _ = owned(request, op_id)
        async with locks.setdefault(op_id, asyncio.Lock()):
            op = db.get(op_id, s["user"]["id"])
            if op["stage"] != "awaiting_password":
                return public_op(op)
            body = await request.json()
            if (
                body.get("saved_password") is not True
                or body.get("key_id") != op["key_id"]
                or not valid_ciphertext(body.get("encrypted_value"))
            ):
                raise ProviderError("password_required")
            save(op, "saving_password")
            tasks[op_id] = asyncio.create_task(launch(op, body["encrypted_value"], s))
        return public_op(op)

    @app.post("/api/operations/{op_id}/retry")
    async def retry(op_id: str, request: Request):
        s, _ = owned(request, op_id)
        async with locks.setdefault(op_id, asyncio.Lock()):
            op = db.get(op_id, s["user"]["id"])
            safe_errors = {
                "template_not_enabled",
                "template_version_changed",
                "permission_denied",
                "authorization_expired",
                "actions_disabled",
                "installation_permissions_missing",
                "not_found",
                "provider_unavailable",
                "rate_limited",
            }
            if op["stage"] != "failed" or op.get("uncertain") or op.get("error") not in safe_errors:
                raise ProviderError("invalid_request")
            if op["mode"] == "named" and not s.get("cloudflare_token"):
                raise ProviderError("authorization_expired")
            op["error"] = ""
            if op.get("secret_written") and op.get("failed_stage") == "launching":
                save(op, "launching")
                tasks[op_id] = asyncio.create_task(launch(op, None, s))
            else:
                save(op, "queued")
                tasks[op_id] = asyncio.create_task(prepare(op, s))
            return public_op(op)

    @app.post("/api/operations/{op_id}/restart")
    async def restart(op_id: str, request: Request):
        s, _ = owned(request, op_id)
        body = await request.json()
        if body.get("accepted_preview") is not True:
            raise ProviderError("invalid_request")
        async with locks.setdefault(op_id, asyncio.Lock()):
            op = db.get(op_id, s["user"]["id"])
            if op["stage"] != "ended" or not op.get("secret_written") or not op.get("run_id"):
                raise ProviderError("invalid_request")
            token = await p.repository_token(op)
            run = await p.get_run(op, token)
            if run.get("status") != "completed":
                raise ProviderError("invalid_request")
            op["previous_run_id"] = op.pop("run_id")
            for key in ("origin", "run_url", "expires_at", "last_seen"):
                op.pop(key, None)
            op.update(error="", uncertain=False, last_sequence=0)
            save(op, "launching")
            tasks[op_id] = asyncio.create_task(launch(op, None, s))
            return public_op(op)

    @app.post("/api/operations/{op_id}/stop")
    async def stop(op_id: str, request: Request):
        s, op = owned(request, op_id)
        if not op.get("run_id") or op["stage"] in TERMINAL:
            raise ProviderError("invalid_request")
        async with locks.setdefault(op_id, asyncio.Lock()):
            op = db.get(op_id, s["user"]["id"])
            if op["stage"] in TERMINAL:
                return public_op(op)
            if op["stage"] != "stopping":
                token = await p.repository_token(op)
                save(op, "stopping")
                op.pop("origin", None)
                save(op)
                try:
                    await p.cancel(op, token)
                except ProviderError as e:
                    op["error"] = e.code
                    save(op)
        return public_op(op)

    @app.post("/api/run-events/{op_id}")
    async def run_event(op_id: str, request: Request):
        candidate = db.get(op_id)
        if (
            not candidate
            or not request.headers.get("authorization", "").startswith("Bearer ")
            or candidate["stage"] not in {"waiting_ready", "ready", "checking_status"}
        ):
            raise ProviderError("invalid_request")
        if c.mode == "mock":
            raise ProviderError("invalid_request")
        claims = await p.oidc_claims(request.headers["authorization"][7:])
        async with locks.setdefault(op_id, asyncio.Lock()):
            if c.mode == "mock":
                raise ProviderError("invalid_request")
            op = db.get(op_id)
            auth = request.headers.get("authorization", "")
            if (
                not op
                or not auth.startswith("Bearer ")
                or op["stage"] not in {"waiting_ready", "ready", "checking_status"}
            ):
                raise ProviderError("invalid_request")
            expected = {
                "repository_id": str(op["repo_id"]),
                "repository_owner_id": str(op["owner_id"]),
                "run_id": str(op["run_id"]),
                "run_attempt": "1",
                "sha": op["commit"],
                "event_name": "workflow_dispatch",
                "ref": "refs/heads/" + op["branch"],
                "workflow_ref": op["owner_login"]
                + "/"
                + op["repo_name"]
                + "/.github/workflows/preview.yml@refs/heads/"
                + op["branch"],
            }
            if any(str(claims.get(k)) != v for k, v in expected.items()):
                raise ProviderError("invalid_run_identity")
            body = await request.json()
            if op.get("dispatch_nonce") and body.get("launch_id") != op["dispatch_nonce"]:
                raise ProviderError("invalid_run_identity")
            if (
                body.get("event") not in {"ready", "ending"}
                or type(body.get("sequence")) is not int
                or body["sequence"] <= op["last_sequence"]
            ):
                raise ProviderError("invalid_request")
            token = await p.repository_token(op)
            run = await p.get_run(op, token)
            if (
                run.get("status") != "in_progress"
                or run.get("head_sha") != op["commit"]
                or run.get("run_attempt") != 1
                or run.get("event") != "workflow_dispatch"
            ):
                raise ProviderError("invalid_run_identity")
            if body["event"] == "ready":
                expiry = body.get("expires_at")
                if (
                    body.get("smoke_passed") is not True
                    or type(expiry) not in {int, float}
                    or not time.time() < expiry <= time.time() + 3660
                    or not accepted_origin(body.get("origin", ""), op)
                ):
                    raise ProviderError("invalid_request")
                try:
                    await check_ready(body["origin"], op)
                except ProviderError as e:
                    op.pop("origin", None)
                    op["error"] = e.code
                    save(op, "checking_status")
                    raise
                op.update(origin=body["origin"], expires_at=expiry, last_seen=time.time(), error="")
                save(op, "ready")
            else:
                op.pop("origin", None)
                save(op, "ending")
            op["last_sequence"] = body["sequence"]
            save(op)
            return {"accepted": True}

    if c.mode == "mock":

        @app.post("/api/mock/inspect")
        async def mock_inspect(request: Request):
            import base64

            from nacl.public import SealedBox

            encrypted = p.secrets.get("BRIDGE_OWNER_TOKEN")
            length = (
                len(SealedBox(p.private_key).decrypt(base64.b64decode(encrypted)))
                if encrypted
                else 0
            )
            return {
                "calls": list(p.calls),
                "owner_password_length": length,
                "ledger_count": len(db.all()),
            }

        @app.post("/api/mock/reset")
        async def mock_reset(request: Request):
            for task in tasks.values():
                task.cancel()
            await asyncio.gather(*tasks.values(), return_exceptions=True)
            tasks.clear()
            db.db.execute("DELETE FROM operations")
            db.db.commit()
            for value in sessions.values():
                for key in ("user", "github_token", "installation_id", "cloudflare_token", "zones"):
                    value.pop(key, None)
            p.calls.clear()
            p.runs.clear()
            p.next_run_id = 987
            p.secrets.clear()
            return {"reset": True}

        @app.post("/api/mock/scenario")
        async def mock_scenario(request: Request):
            body = await request.json()
            allowed = {
                "success",
                "no_zones",
                "authorization_expired",
                "template_not_enabled",
                "repository_name_taken",
                "permission_denied",
                "actions_disabled",
                "create_unknown",
                "hostname_taken",
                "dispatch_unknown",
                "launch_failed",
                "readiness_failed",
            }
            if body.get("scenario") not in allowed:
                raise ProviderError("invalid_request")
            p.scenario = body["scenario"]
            return {"scenario": p.scenario}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


app = create_app()
