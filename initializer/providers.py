"""Real provider adapters. Only explicit calls mutate; no secret-bearing response logging."""

import base64
import hashlib
import re
import time
from datetime import datetime
from urllib.parse import quote

import httpx
import jwt
from nacl.public import PublicKey, SealedBox


class ProviderError(Exception):
    def __init__(self, code, uncertain=False):
        self.code, self.uncertain = code, uncertain
        super().__init__(code)


def sealed(value, public_key):
    return base64.b64encode(
        SealedBox(PublicKey(base64.b64decode(public_key))).encrypt(value.encode())
    ).decode()


def segment(value):
    return quote(str(value), safe="")


class Providers:
    def __init__(self, config, client=None):
        self.config = config
        self.http = client or httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False)
        self.repo_tokens = {}
        self.jwks_cache = None
        self.jwks_expiry = 0

    async def request(self, method, url, token=None, body=None, form=None):
        headers = {"Accept": "application/json", "X-GitHub-Api-Version": "2026-03-10"}
        if token:
            headers["Authorization"] = "Bearer " + token
        try:
            r = await self.http.request(method, url, headers=headers, json=body, data=form)
        except httpx.HTTPError:
            raise ProviderError(
                "provider_unavailable", uncertain=method not in {"GET", "HEAD"}
            ) from None
        if r.status_code >= 300:
            code = {
                401: "authorization_expired",
                403: "permission_denied",
                404: "not_found",
                409: "resource_conflict",
                422: "resource_conflict",
                429: "rate_limited",
            }.get(r.status_code, "provider_unavailable")
            raise ProviderError(code, uncertain=r.status_code >= 500 and method != "GET")
        if not r.content:
            return {}
        try:
            data = r.json()
        except ValueError:
            raise ProviderError("unexpected_provider_response", uncertain=method != "GET") from None
        if url.startswith("https://api.cloudflare.com/"):
            if not data.get("success"):
                raise ProviderError("cloudflare_request_failed", uncertain=method != "GET")
            return data["result"]
        return data

    async def gh(self, method, path, token=None, body=None):
        return await self.request(method, "https://api.github.com" + path, token, body)

    async def cf(self, method, path, token, body=None):
        return await self.request(
            method, "https://api.cloudflare.com/client/v4" + path, token, body
        )

    async def exchange(self, provider, code, verifier):
        c = self.config
        if provider == "github":
            data = await self.request(
                "POST",
                "https://github.com/login/oauth/access_token",
                body={
                    "client_id": c.github_client_id,
                    "client_secret": c.github_client_secret,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": c.origin + "/auth/github/callback",
                },
            )
        else:
            data = await self.request(
                "POST",
                "https://dash.cloudflare.com/oauth2/token",
                form={
                    "grant_type": "authorization_code",
                    "client_id": c.cf_client_id,
                    "client_secret": c.cf_client_secret,
                    "code": code,
                    "code_verifier": verifier,
                    "redirect_uri": c.origin + "/auth/cloudflare/callback",
                },
            )
        if not data.get("access_token"):
            raise ProviderError("authorization_declined")
        return data["access_token"]

    async def identity(self, token):
        user = await self.gh("GET", "/user", token)
        if user.get("type") != "User" or not isinstance(user.get("id"), int):
            raise ProviderError("personal_account_required")
        return {"id": user["id"], "login": user["login"]}

    async def installation(self, token, user):
        # Deliberately support personal accounts first. Do not guess org ownership.
        for page in range(1, 11):
            data = await self.gh("GET", f"/user/installations?per_page=100&page={page}", token)
            for item in data.get("installations", []):
                if (
                    str(item.get("app_id")) == self.config.github_app_id
                    and item.get("account", {}).get("id") == user["id"]
                    and not item.get("suspended_at")
                ):
                    permissions = item.get("permissions", {})
                    if (
                        permissions.get("secrets") != "write"
                        or permissions.get("actions") != "write"
                        or permissions.get("contents") not in {"read", "write"}
                    ):
                        raise ProviderError("installation_permissions_missing")
                    return item["id"]
            if len(data.get("installations", [])) < 100:
                break
        raise ProviderError("installation_required")

    async def repository_token(self, op):
        now = int(time.time())
        cache_key = (op["installation_id"], op["repo_id"])
        cached = self.repo_tokens.get(cache_key)
        if cached and cached[1] > now:
            return cached[0]
        self.repo_tokens = {k: v for k, v in self.repo_tokens.items() if v[1] > now}
        token = jwt.encode(
            {"iat": now - 30, "exp": now + 540, "iss": self.config.github_app_id},
            self.config.github_private_key,
            algorithm="RS256",
        )
        result = await self.gh(
            "POST",
            f"/app/installations/{op['installation_id']}/access_tokens",
            token,
            {
                "repository_ids": [op["repo_id"]],
                "permissions": {"contents": "read", "actions": "write", "secrets": "write"},
            },
        )
        if not result.get("token"):
            raise ProviderError("installation_permissions_missing")
        self.repo_tokens[cache_key] = (result["token"], now + 3000)
        return result["token"]

    async def create_repository(self, op, user_token):
        template = self.config.template
        source = await self.gh("GET", f"/repos/{template}", user_token)
        if source.get("is_template") is not True:
            raise ProviderError("template_not_enabled")
        head = await self.gh(
            "GET", f"/repos/{template}/commits/{segment(source['default_branch'])}", user_token
        )
        if head["sha"] != self.config.template_sha:
            raise ProviderError("template_version_changed")
        path = f"/repos/{op['owner_login']}/{op['repo_name']}"
        try:
            await self.gh("GET", path, user_token)
        except ProviderError as e:
            if e.code != "not_found":
                raise
        else:
            raise ProviderError("repository_name_taken")
        result = await self.gh(
            "POST",
            f"/repos/{template}/generate",
            user_token,
            {
                "owner": op["owner_login"],
                "name": op["repo_name"],
                "private": False,
                "include_all_branches": False,
                "description": "My single-user desktop-bridge development preview",
            },
        )
        if result.get("owner", {}).get("id") != op["owner_id"]:
            raise ProviderError("repository_owner_mismatch", uncertain=True)
        return {
            "repo_id": result["id"],
            "repo_url": result["html_url"],
            "branch": result["default_branch"],
        }

    async def verify_repository(self, op, token):
        path = f"/repos/{op['owner_login']}/{op['repo_name']}"
        meta = await self.gh("GET", path, token)
        if meta["id"] != op["repo_id"] or meta["owner"]["id"] != op["owner_id"]:
            raise ProviderError("repository_owner_mismatch")
        head = await self.gh("GET", path + "/commits/" + segment(op["branch"]), token)
        source = await self.gh(
            "GET", f"/repos/{self.config.template}/commits/{self.config.template_sha}"
        )
        if head["commit"]["tree"]["sha"] != source["commit"]["tree"]["sha"]:
            raise ProviderError("repository_version_changed")
        workflow = await self.gh("GET", path + "/actions/workflows/preview.yml", token)
        if workflow.get("state") not in {"active", "disabled_manually", "disabled_inactivity"}:
            raise ProviderError("actions_disabled")
        return head["sha"]

    async def public_key(self, op, token):
        return await self.gh("GET", self.repo_path(op) + "/actions/secrets/public-key", token)

    @staticmethod
    def repo_path(op):
        return f"/repos/{op['owner_login']}/{op['repo_name']}"

    async def write_secret(self, op, token, name, encrypted, key_id):
        await self.gh(
            "PUT",
            self.repo_path(op) + "/actions/secrets/" + name,
            token,
            {"encrypted_value": encrypted, "key_id": key_id},
        )

    async def dispatch(self, op, token):
        path = self.repo_path(op) + "/actions/workflows/preview.yml"
        await self.gh("PUT", path + "/enable", token)
        result = await self.gh(
            "POST",
            path + "/dispatches",
            token,
            {
                "ref": op["branch"],
                "inputs": {
                    "mode": "preview",
                    "tunnel": op["mode"],
                    "minutes": "60",
                    "public_url": "https://" + op["hostname"] if op.get("hostname") else "",
                    "operation_id": op["id"],
                    "launch_id": op["dispatch_nonce"],
                    "initializer_url": self.config.origin,
                },
            },
        )
        run_id = result.get("workflow_run_id")
        if not isinstance(run_id, int):
            # A 204/timeout may mean a run exists. Do NOT dispatch again.
            raise ProviderError("dispatch_result_unknown", uncertain=True)
        return {"run_id": run_id, "run_url": result.get("html_url", "")}

    async def find_dispatched_run(self, op, token):
        if not op.get("dispatch_nonce"):
            return None
        data = await self.gh(
            "GET",
            self.repo_path(op)
            + "/actions/workflows/preview.yml/runs?event=workflow_dispatch&per_page=100",
            token,
        )
        matches = []
        for run in data.get("workflow_runs", []):
            try:
                created = datetime.fromisoformat(
                    run["created_at"].replace("Z", "+00:00")
                ).timestamp()
            except (ValueError, KeyError):
                continue
            if (
                run.get("display_title") == "MCP preview " + op["id"] + " " + op["dispatch_nonce"]
                and run.get("head_sha") == op["commit"]
                and run.get("event") == "workflow_dispatch"
                and run.get("run_attempt") == 1
                and run.get("id") != op.get("previous_run_id")
                and created >= op.get("dispatch_at", op["created_at"]) - 30
            ):
                matches.append(run)
        if len(matches) == 1:
            return {"run_id": matches[0]["id"], "run_url": matches[0]["html_url"]}
        return None  # Zero or ambiguous matches are not permission to redispatch.

    async def get_run(self, op, token):
        return await self.gh("GET", self.repo_path(op) + f"/actions/runs/{op['run_id']}", token)

    async def cancel(self, op, token):
        await self.gh("POST", self.repo_path(op) + f"/actions/runs/{op['run_id']}/cancel", token)

    async def zones(self, token):
        # Only select from provider-verified active zones. No arbitrary account/host inputs.
        result = []
        for page in range(1, 11):
            zones = await self.cf("GET", f"/zones?status=active&per_page=50&page={page}", token)
            result.extend(
                {"id": z["id"], "name": z["name"], "account_id": z["account"]["id"]}
                for z in zones
                if z.get("status") == "active"
            )
            if len(zones) < 50:
                break
        return result

    async def check_hostname(self, op, token):
        zone = await self.cf("GET", f"/zones/{segment(op['zone_id'])}", token)
        if (
            zone.get("status") != "active"
            or zone["account"]["id"] != op["account_id"]
            or zone["name"] != op["zone_name"]
        ):
            raise ProviderError("zone_unavailable")
        records = await self.cf(
            "GET",
            f"/zones/{segment(op['zone_id'])}/dns_records?name={segment(op['hostname'])}",
            token,
        )
        if records:
            raise ProviderError("hostname_taken")

    async def create_tunnel(self, op, token):
        return await self.cf(
            "POST",
            f"/accounts/{segment(op['account_id'])}/cfd_tunnel",
            token,
            {"name": "desktop-bridge-" + op["id"], "config_src": "cloudflare"},
        )

    async def configure_tunnel(self, op, token):
        path = f"/accounts/{segment(op['account_id'])}/cfd_tunnel/{segment(op['tunnel_id'])}"
        await self.cf(
            "PUT",
            path + "/configurations",
            token,
            {
                "config": {
                    "ingress": [
                        {"hostname": op["hostname"], "service": "http://127.0.0.1:8080"},
                        {"service": "http_status:404"},
                    ]
                }
            },
        )
        dns = await self.cf(
            "POST",
            f"/zones/{segment(op['zone_id'])}/dns_records",
            token,
            {
                "type": "CNAME",
                "name": op["hostname"],
                "content": op["tunnel_id"] + ".cfargotunnel.com",
                "proxied": True,
                "comment": "desktop-bridge operation " + op["id"],
            },
        )
        return dns["id"]

    async def verify_tunnel(self, op, token):
        zone = await self.cf("GET", f"/zones/{segment(op['zone_id'])}", token)
        if (
            zone.get("status") != "active"
            or zone.get("account", {}).get("id") != op["account_id"]
            or zone.get("name") != op["zone_name"]
        ):
            raise ProviderError("zone_unavailable")
        record = await self.cf(
            "GET", f"/zones/{segment(op['zone_id'])}/dns_records/{segment(op['dns_id'])}", token
        )
        if (
            record.get("type") != "CNAME"
            or record.get("name") != op["hostname"]
            or record.get("content") != op["tunnel_id"] + ".cfargotunnel.com"
            or record.get("proxied") is not True
        ):
            raise ProviderError("tunnel_binding_changed")
        config = await self.cf(
            "GET",
            f"/accounts/{segment(op['account_id'])}/cfd_tunnel/{segment(op['tunnel_id'])}/configurations",
            token,
        )
        rules = config.get("config", {}).get("ingress", [])
        if (
            len(rules) != 2
            or rules[0].get("hostname") != op["hostname"]
            or rules[0].get("service") != "http://127.0.0.1:8080"
            or rules[0].get("path")
            or rules[1].get("service") != "http_status:404"
            or rules[1].get("hostname")
            or rules[1].get("path")
        ):
            raise ProviderError("tunnel_binding_changed")

    async def tunnel_token(self, op, token):
        return await self.cf(
            "GET",
            f"/accounts/{segment(op['account_id'])}/cfd_tunnel/{segment(op['tunnel_id'])}/token",
            token,
        )

    async def oidc_claims(self, token):
        # Never follow jku/x5u from a token. The issuer's fixed JWKS is the only source.
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise ValueError()
            if not self.jwks_cache or self.jwks_expiry <= time.time():
                self.jwks_cache = await self.request(
                    "GET", "https://token.actions.githubusercontent.com/.well-known/jwks"
                )
                self.jwks_expiry = time.time() + 300
            key = next(k for k in self.jwks_cache["keys"] if k["kid"] == header.get("kid"))
            return jwt.decode(
                token,
                jwt.PyJWK.from_dict(key).key,
                algorithms=["RS256"],
                audience=self.config.origin,
                issuer="https://token.actions.githubusercontent.com",
                options={"require": ["exp", "iat", "nbf", "iss", "aud", "sub"]},
            )
        except (jwt.PyJWTError, ValueError, KeyError, StopIteration):
            raise ProviderError("invalid_run_identity") from None


def oauth_challenge(verifier):
    return (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    )


def valid_ciphertext(value):
    try:
        raw = base64.b64decode(value, validate=True)
        return 80 <= len(raw) <= 1024
    except (ValueError, TypeError):
        return False


def valid_repo_name(value):
    return bool(re.fullmatch(r"desktop-bridge-[a-f0-9]{12}", value))
