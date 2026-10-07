"""Prevent caches from retaining authenticated API responses, including errors."""
from starlette.datastructures import MutableHeaders
from fastapi.exceptions import RequestValidationError
from starlette.responses import JSONResponse


async def safe_validation_error(request, exception: RequestValidationError):
    # Pydantic's default response includes rejected input, which may contain
    # pasted VPN material or tokens. Clients need only the fixed status/message.
    return JSONResponse({"detail": "invalid request"}, status_code=422,
                        headers={"Cache-Control": "no-store", "Pragma": "no-cache"})


class ApiNoStoreMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope["path"].startswith("/api/"):
            return await self.app(scope, receive, send)

        async def send_no_store(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Pragma"] = "no-cache"
            await send(message)

        await self.app(scope, receive, send_no_store)
