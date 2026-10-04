"""Small bounded HTTP-body guard, including chunked requests."""

from __future__ import annotations

import asyncio

from starlette.responses import Response


class BodyLimitMiddleware:
    def __init__(self, app, limit=4 * 1024 * 1024):
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        body = bytearray()
        try:
            async with asyncio.timeout(15):
                while True:
                    message = await receive()
                    if message["type"] == "http.disconnect":
                        return
                    body.extend(message.get("body", b""))
                    if len(body) > self.limit:
                        return await Response(status_code=413)(scope, receive, send)
                    if not message.get("more_body"):
                        break
        except TimeoutError:
            return await Response(status_code=408)(scope, receive, send)
        delivered = False

        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": bytes(body), "more_body": False}
            return await receive()

        return await self.app(scope, replay, send)
