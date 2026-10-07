import logging
import re
import time
import uuid

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from shikumi_app.logs import request_id

log = logging.getLogger("shikumi_app.request")
HEADER = "x-request-id"
_SAFE_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")


class RequestIdMiddleware:
    """Reads X-Request-ID (or makes one), puts it on every log line and on the response, and logs each request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        given = dict(scope["headers"]).get(HEADER.encode(), b"").decode("latin-1")
        rid = given if _SAFE_ID.fullmatch(given) else uuid.uuid4().hex
        token = request_id.set(rid)
        start, status = time.perf_counter(), 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [*message.get("headers", []), (HEADER.encode(), rid.encode())]
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            log.info(
                "request",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status": status,
                    "duration_ms": round((time.perf_counter() - start) * 1000, 1),
                },
            )
            request_id.reset(token)
