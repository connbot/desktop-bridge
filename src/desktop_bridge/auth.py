"""Single-owner OAuth 2.1 authorization code + S256 PKCE and UI sessions.

Deliberately in-memory: restart revokes grants, never restores unknown access.
No upstream model credentials or subscriber cookies are accepted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
from dataclasses import dataclass
from threading import RLock
from urllib.parse import urlsplit

from .state import BridgeError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


ACCESS_TOKEN_SECONDS = 3600
GRANT_SECONDS = 30 * 24 * 3600
REFRESH_IDLE_SECONDS = 7 * 24 * 3600
MAX_GRANTS = 100
MAX_REFRESH_TOKENS = 100_000


@dataclass
class OAuthGrant:
    client_id: str
    resource: str
    scope: str
    expires_at: float
    refresh_expires_at: float
    current_refresh: str | None = None


class Auth:
    def __init__(self, owner_token: str, base_url: str):
        if not 8 <= len(owner_token) <= 256 or not owner_token.strip():
            raise ValueError("BRIDGE_OWNER_TOKEN must contain 8–256 characters and not be blank")
        self.owner_hash = digest(owner_token)
        self.base_url = base_url.rstrip("/")
        self.clients = {}
        self.codes = {}
        self.tokens = {}
        self.grants: dict[str, OAuthGrant] = {}
        # Keep consumed hashes until their grant ends to detect refresh replay.
        self.refresh_tokens: dict[str, str] = {}
        self._lock = RLock()
        self.sessions = {}
        self.attempts = {}

    def throttle(self, key: str):
        now = time.monotonic()
        self.attempts = {k: v for k, v in self.attempts.items() if now - v[0] < 60}
        count = self.attempts.get(key, (now, 0))[1]
        if count >= 20 or len(self.attempts) >= 1000:
            raise BridgeError("RATE_LIMITED", "Too many requests, retry later")
        self.attempts[key] = (self.attempts.get(key, (now, 0))[0], count + 1)

    def login(self, token: str):
        if not hmac.compare_digest(digest(token), self.owner_hash):
            raise BridgeError("UNAUTHORIZED", "Invalid owner token")
        sid, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(24)
        self.sessions = {k: v for k, v in self.sessions.items() if v[1] > time.time()}
        if len(self.sessions) >= 16:
            self.sessions.pop(next(iter(self.sessions)))
        self.sessions[digest(sid)] = (csrf, time.time() + 8 * 3600)
        return sid, csrf

    def session(self, sid: str | None):
        data = self.sessions.get(digest(sid or ""))
        return data if data and data[1] > time.time() else None

    def check_csrf(self, sid: str | None, csrf: str | None):
        data = self.session(sid)
        if not data or not hmac.compare_digest(data[0], csrf or ""):
            raise BridgeError("UNAUTHORIZED", "Login and provide a valid CSRF token")

    def bearer(self, token: str):
        # Owner bootstrap token intentionally cannot be used as an MCP token.
        with self._lock:
            item = self.tokens.get(digest(token))
            if not item:
                return False
            grant = self.grants.get(item[0])
            now = time.time()
            return bool(grant and item[1] > now and grant.expires_at > now)

    def revoke(self):
        with self._lock:
            self.tokens.clear()
            self.grants.clear()
            self.refresh_tokens.clear()
            self.codes.clear()
            self.sessions.clear()

    def register(self, data):
        if not isinstance(data, dict):
            raise BridgeError("INVALID_CLIENT", "Client metadata must be an object")
        redirects = data.get("redirect_uris")
        if not isinstance(redirects, list) or not 1 <= len(redirects) <= 10:
            raise BridgeError("INVALID_CLIENT", "Supply 1–10 redirect_uris")
        for uri in redirects:
            if not isinstance(uri, str) or len(uri) > 2048 or any(ord(c) <= 32 for c in uri):
                raise BridgeError("INVALID_CLIENT", "Invalid redirect URI")
            parsed = urlsplit(uri)
            if (
                not parsed.hostname
                or parsed.fragment
                or parsed.username
                or parsed.password
                or not (
                    parsed.scheme == "https"
                    or (
                        parsed.scheme == "http"
                        and parsed.hostname in {"localhost", "127.0.0.1", "::1"}
                    )
                )
            ):
                raise BridgeError("INVALID_CLIENT", "Redirect must be HTTPS or loopback HTTP")
        if len(self.clients) >= 100:
            raise BridgeError("REGISTRY_FULL", "Restart service to clear unused clients")
        if data.get("token_endpoint_auth_method", "none") != "none":
            raise BridgeError("INVALID_CLIENT", "Only public PKCE clients are supported")
        grant_types = data.get("grant_types", ["authorization_code"])
        if (
            not isinstance(grant_types, list)
            or not all(isinstance(value, str) for value in grant_types)
            or "authorization_code" not in grant_types
            or not set(grant_types) <= {"authorization_code", "refresh_token"}
        ):
            raise BridgeError("INVALID_CLIENT", "Unsupported grant_types")
        client = secrets.token_urlsafe(24)
        self.clients[client] = {
            "client_id": client,
            "redirect_uris": redirects,
            "client_name": str(data.get("client_name", "MCP client"))[:120],
            "token_endpoint_auth_method": "none",
            "grant_types": list(dict.fromkeys(grant_types)),
            "response_types": ["code"],
        }
        return self.clients[client]

    def validate_request(self, params):
        client = self.clients.get(params.get("client_id"))
        if not client or params.get("redirect_uri") not in client["redirect_uris"]:
            raise BridgeError("INVALID_REQUEST", "Unregistered client or redirect URI")
        if params.get("response_type") != "code" or params.get("code_challenge_method") != "S256":
            raise BridgeError("INVALID_REQUEST", "Authorization code with S256 PKCE is required")
        if not re.fullmatch(r"[A-Za-z0-9_-]{43}", params.get("code_challenge", "")):
            raise BridgeError("INVALID_REQUEST", "Invalid PKCE challenge")
        if params.get("resource") not in {None, self.base_url + "/mcp"}:
            raise BridgeError("INVALID_TARGET", "Wrong resource")
        if params.get("scope", "computer") != "computer":
            raise BridgeError("INVALID_SCOPE", "Only computer scope is supported")
        if len(params.get("state", "")) > 2048:
            raise BridgeError("INVALID_REQUEST", "State too long")
        return client

    def approve(self, params):
        self.validate_request(params)
        self.codes = {k: v for k, v in self.codes.items() if v[1] > time.time()}
        if len(self.codes) >= 100:
            raise BridgeError("RATE_LIMITED", "Too many pending grants")
        code = secrets.token_urlsafe(32)
        self.codes[digest(code)] = (dict(params), time.time() + 90)
        return code

    def exchange(self, data):
        if not isinstance(data, dict) or any(
            not isinstance(key, str) or not isinstance(value, str) or len(value) > 4096
            for key, value in data.items()
        ):
            raise BridgeError("INVALID_REQUEST", "Invalid token request")
        # Rotation and revocation are one atomic operation, including callers in
        # different threads. State remains deliberately local to one process.
        with self._lock:
            now = time.time()
            self._prune(now)
            if data.get("grant_type") == "refresh_token":
                return self._refresh(data, now)
            if data.get("grant_type") != "authorization_code":
                raise BridgeError(
                    "UNSUPPORTED_GRANT_TYPE", "Use authorization_code or refresh_token"
                )
            return self._exchange_code(data, now)

    def _exchange_code(self, data, now):
        item = self.codes.pop(digest(data.get("code", "")), None)
        if not item or item[1] <= now:
            raise BridgeError("INVALID_GRANT", "Expired or used authorization code")
        params = item[0]
        verifier = data.get("code_verifier", "")
        if not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier):
            raise BridgeError("INVALID_GRANT", "Invalid PKCE verifier")
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
            .decode()
            .rstrip("=")
        )
        if (
            not hmac.compare_digest(challenge, params["code_challenge"])
            or data.get("client_id") != params["client_id"]
            or data.get("redirect_uri") != params["redirect_uri"]
            or data.get("resource") not in {None, self.base_url + "/mcp"}
        ):
            raise BridgeError("INVALID_GRANT", "Authorization binding failed")
        client = self.clients.get(params["client_id"])
        if not client:
            raise BridgeError("INVALID_GRANT", "Unregistered client")
        renewable = "refresh_token" in client["grant_types"]
        if len(self.grants) >= MAX_GRANTS or (
            renewable and len(self.refresh_tokens) >= MAX_REFRESH_TOKENS
        ):
            raise BridgeError("RATE_LIMITED", "Too many active grants")
        grant_id = secrets.token_urlsafe(24)
        self.grants[grant_id] = OAuthGrant(
            client_id=params["client_id"],
            resource=self.base_url + "/mcp",
            scope="computer",
            expires_at=now + (GRANT_SECONDS if renewable else ACCESS_TOKEN_SECONDS),
            refresh_expires_at=now + REFRESH_IDLE_SECONDS,
        )
        return self._issue(grant_id, now, renewable=renewable)

    def _refresh(self, data, now):
        token_hash = digest(data.get("refresh_token", ""))
        grant_id = self.refresh_tokens.get(token_hash)
        grant = self.grants.get(grant_id)
        if not grant:
            raise BridgeError("INVALID_GRANT", "Invalid or expired refresh token")
        if (
            data.get("client_id") != grant.client_id
            or grant.client_id not in self.clients
            or data.get("resource") not in {None, grant.resource}
        ):
            raise BridgeError("INVALID_GRANT", "Refresh token binding failed")
        if data.get("scope", grant.scope) != grant.scope:
            raise BridgeError("INVALID_SCOPE", "Only the granted computer scope is allowed")
        # Check binding before consuming or revoking anything. A request from a
        # different client/resource must not burn the legitimate client's grant.
        if not hmac.compare_digest(token_hash, grant.current_refresh or ""):
            self._revoke_grant(grant_id)
            raise BridgeError("INVALID_GRANT", "Refresh token reuse; reconnect the client")
        if len(self.refresh_tokens) >= MAX_REFRESH_TOKENS:
            raise BridgeError("RATE_LIMITED", "Refresh capacity reached; retry later")
        grant.refresh_expires_at = min(grant.expires_at, now + REFRESH_IDLE_SECONDS)
        return self._issue(grant_id, now, renewable=True)

    def _issue(self, grant_id, now, *, renewable):
        grant = self.grants[grant_id]
        expires_in = min(ACCESS_TOKEN_SECONDS, int(grant.expires_at - now))
        if expires_in <= 0:
            self._revoke_grant(grant_id)
            raise BridgeError("INVALID_GRANT", "Authorization expired; reconnect the client")
        token = secrets.token_urlsafe(32)
        self.tokens[digest(token)] = (grant_id, now + expires_in)
        result = {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": expires_in,
            "scope": grant.scope,
        }
        if renewable:
            refresh = secrets.token_urlsafe(32)
            grant.current_refresh = digest(refresh)
            self.refresh_tokens[grant.current_refresh] = grant_id
            result["refresh_token"] = refresh
        return result

    def _revoke_grant(self, grant_id):
        self.grants.pop(grant_id, None)
        self.tokens = {k: v for k, v in self.tokens.items() if v[0] != grant_id}
        self.refresh_tokens = {k: v for k, v in self.refresh_tokens.items() if v != grant_id}

    def _prune(self, now):
        self.grants = {
            k: v for k, v in self.grants.items()
            if v.expires_at > now and v.refresh_expires_at > now
        }
        self.tokens = {
            k: v for k, v in self.tokens.items() if v[1] > now and v[0] in self.grants
        }
        self.refresh_tokens = {
            k: v for k, v in self.refresh_tokens.items() if v in self.grants
        }
