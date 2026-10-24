"""Explicit configuration. Missing credentials never imply a simulated production run."""

import os
from dataclasses import dataclass
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Config:
    mode: str = "live"
    origin: str = "http://127.0.0.1:8765"
    database: str = "initializer-state.sqlite3"
    github_app_id: str = ""
    github_slug: str = ""
    github_client_id: str = ""
    github_client_secret: str = ""
    github_private_key: str = ""
    template: str = "connbot/desktop-bridge"
    template_sha: str = ""
    cf_client_id: str = ""
    cf_client_secret: str = ""
    cf_scopes: str = ""
    session_seconds: int = 7200

    @classmethod
    def from_env(cls):
        return cls(
            **{
                name: os.environ.get("INITIALIZER_" + name.upper(), field.default)
                for name, field in cls.__dataclass_fields__.items()
                if name != "session_seconds"
            }
        )

    def validate(self):
        p = urlsplit(self.origin)
        if p.path or p.query or p.fragment or p.username or p.password:
            raise ValueError("INITIALIZER_ORIGIN must be an origin, without path or credentials")
        if self.mode not in {"live", "mock"}:
            raise ValueError("INITIALIZER_MODE must be live or mock")
        if self.mode == "mock" and p.hostname not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("Mock mode is restricted to loopback")
        if self.github_ready and p.scheme != "https":
            raise ValueError("Configured live authentication requires HTTPS")

    @property
    def github_ready(self):
        return self.mode == "live" and all(
            (
                self.github_app_id,
                self.github_slug,
                self.github_client_id,
                self.github_client_secret,
                self.github_private_key,
                self.template_sha,
            )
        )

    @property
    def cloudflare_ready(self):
        return self.github_ready and all((self.cf_client_id, self.cf_client_secret, self.cf_scopes))
