"""Bound API request streams before request bodies are buffered."""

from fastapi import HTTPException

MAX_BODY_BYTES = 5_000_000


class BodyLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)
        size = 0

        async def limited_receive():
            nonlocal size
            message = await receive()
            if message["type"] == "http.request":
                size += len(message.get("body", b""))
                if size > MAX_BODY_BYTES:
                    raise HTTPException(413, "Payload exceeds 5 MB")
            return message

        await self.app(scope, limited_receive, send)
