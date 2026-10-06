"""Credential-free readiness reporting, authenticated with GitHub Actions OIDC.

Never sends the owner password, tunnel token, runtime request token, or GitHub API
credentials to the initializer. The GitHub OIDC JWT is audience-bound to its HTTPS
origin. Disable completely when no initializer operation was explicitly provided.
"""

import ipaddress
import os
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


def report_config(env):
    operation = env.get("INITIALIZER_OPERATION", "")
    origin = env.get("INITIALIZER_URL", "")
    launch_id = env.get("INITIALIZER_LAUNCH_ID", "")
    if launch_id and not re.fullmatch(r"[a-f0-9]{32}", launch_id):
        raise ValueError("Invalid initializer launch ID")
    if not operation and not origin:
        return None
    if not re.fullmatch(r"[a-f0-9]{32}", operation):
        raise ValueError("Invalid initializer operation ID")
    p = urlsplit(origin)
    if (
        p.scheme != "https"
        or not p.hostname
        or p.port not in {None, 443}
        or p.path
        or p.query
        or p.fragment
        or p.username
        or p.password
        or not re.fullmatch(r"[a-z0-9.-]+", p.hostname)
        or "." not in p.hostname
    ):
        raise ValueError("Initializer must be an HTTPS origin")
    try:
        ipaddress.ip_address(p.hostname)
    except ValueError:
        pass
    else:
        raise ValueError("Initializer must use a public DNS hostname")
    if p.hostname.endswith((".localhost", ".local", ".internal")):
        raise ValueError("Initializer must use a public DNS hostname")
    return origin, operation


def oidc_request_url(value, audience):
    p = urlsplit(value)
    if (
        p.scheme != "https"
        or not p.hostname
        or not p.hostname.endswith(".actions.githubusercontent.com")
        or p.username
        or p.password
        or p.port not in {None, 443}
        or p.fragment
    ):
        raise ValueError("Unexpected GitHub OIDC endpoint")
    query = [(k, v) for k, v in parse_qsl(p.query) if k != "audience"]
    query.append(("audience", audience))
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(query), ""))


def report(event, sequence, *, origin="", expires_at=0, env=None, client=None):
    env = os.environ if env is None else env
    config = report_config(env)
    if config is None:
        return False
    if event not in {"ready", "ending"}:
        raise ValueError("Unexpected initializer event")
    target, operation = config
    owns_client = client is None
    client = client or httpx.Client(timeout=12, follow_redirects=False)
    try:
        url = oidc_request_url(env.get("ACTIONS_ID_TOKEN_REQUEST_URL", ""), target)
        token_response = client.get(
            url,
            headers={"Authorization": "Bearer " + env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN", "")},
        )
        token_response.raise_for_status()
        assertion = token_response.json()["value"]
        payload = {"event": event, "sequence": sequence}
        if env.get("INITIALIZER_LAUNCH_ID"):
            payload["launch_id"] = env["INITIALIZER_LAUNCH_ID"]
        if event == "ready":
            payload.update(origin=origin, expires_at=expires_at, smoke_passed=True)
        result = client.post(
            target + "/api/run-events/" + operation,
            headers={"Authorization": "Bearer " + assertion},
            json=payload,
        )
        return result.status_code == 200
    except (httpx.HTTPError, ValueError, KeyError):
        # Do not include exception strings: URLs, headers and raw bodies can contain tokens.
        return False
    finally:
        if owns_client:
            client.close()
