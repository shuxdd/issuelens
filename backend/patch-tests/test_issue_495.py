import asyncio

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route


async def endpoint(request):
    assert await request.body() == b"a=1"
    return PlainTextResponse("ok")


class ReadBody(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        assert await request.body() == b"a=1"
        return await call_next(request)


async def main():
    app = Starlette(routes=[Route("/", endpoint, methods=["POST"])])
    app.add_middleware(ReadBody)
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "root_path": "",
        "headers": [],
    }
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b"a=1", "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        pass

    try:
        await asyncio.wait_for(app(scope, receive, send), 2)
    except TimeoutError:
        raise AssertionError("request body was not replayed to downstream app") from None


asyncio.run(main())
