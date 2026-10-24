"""Credential-free readiness probes with fixed HTTPS host and pinned public DNS."""

import asyncio
import ipaddress
import re
import socket
from urllib.parse import urlsplit

import httpx

from .providers import ProviderError


def accepted_origin(value, op):
    try:
        p = urlsplit(value)
        if (
            p.scheme != "https"
            or p.port not in {None, 443}
            or p.path
            or p.query
            or p.fragment
            or p.username
            or p.password
            or not p.hostname
            or value != "https://" + p.hostname
        ):
            return False
        if op["mode"] == "named":
            return p.hostname == op.get("hostname")
        return bool(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*\.trycloudflare\.com", p.hostname))
    except ValueError:
        return False


async def probe(origin, op):
    if not accepted_origin(origin, op):
        raise ProviderError("invalid_preview_origin")
    host = urlsplit(origin).hostname
    try:
        addresses = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        raise ProviderError("readiness_pending") from None
    ips = {item[4][0] for item in addresses}
    if not ips or not all(ipaddress.ip_address(ip).is_global for ip in ips):
        raise ProviderError("invalid_preview_origin")
    ip = sorted(ips)[0]
    base = "https://" + (f"[{ip}]" if ":" in ip else ip)
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=False, trust_env=False) as client:
            for path in ("/healthz", "/.well-known/oauth-authorization-server"):
                r = await client.get(
                    base + path, headers={"Host": host}, extensions={"sni_hostname": host}
                )
                if r.status_code != 200:
                    raise ProviderError("readiness_pending")
                body = r.json()
                if path == "/healthz" and body.get("ready") is not True:
                    raise ProviderError("readiness_pending")
                if path != "/healthz" and body.get("issuer") != origin:
                    raise ProviderError("readiness_pending")
    except (httpx.HTTPError, ValueError):
        raise ProviderError("readiness_pending") from None
