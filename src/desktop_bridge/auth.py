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
from urllib.parse import urlsplit

from .state import BridgeError


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Auth:
    def __init__(self, owner_token: str, base_url: str):
        if not 8 <= len(owner_token) <= 256 or not owner_token.strip():
            raise ValueError("BRIDGE_OWNER_TOKEN must contain 8–256 characters and not be blank")
        self.owner_hash = digest(owner_token)
        self.base_url = base_url.rstrip("/")
        self.clients = {}
        self.codes = {}
        self.tokens = {}
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
        return self.tokens.get(digest(token), 0) > time.time()

    def revoke(self):
        self.tokens.clear()
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
        client = secrets.token_urlsafe(24)
        self.clients[client] = {
            "client_id": client,
            "redirect_uris": redirects,
            "client_name": str(data.get("client_name", "MCP client"))[:120],
            "token_endpoint_auth_method": "none",
            "grant_types": ["authorization_code"],
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
        if data.get("grant_type") != "authorization_code":
            raise BridgeError("UNSUPPORTED_GRANT_TYPE", "Use authorization_code")
        item = self.codes.pop(digest(data.get("code", "")), None)
        if not item or item[1] < time.time():
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
        self.tokens = {k: v for k, v in self.tokens.items() if v > time.time()}
        token = secrets.token_urlsafe(32)
        self.tokens[digest(token)] = time.time() + 3600
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "computer",
        }
