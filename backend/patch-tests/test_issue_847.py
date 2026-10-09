import asyncio

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import PlainTextResponse
from starlette.routing import Route


async def endpoint(request):
    assert await request.json() == {"a": 1}
    return PlainTextResponse("ok")


class InspectAfter(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        assert await request.json() == {"a": 1}
        return response


async def main():
    app = Starlette(routes=[Route("/", endpoint, methods=["POST"])])
    app.add_middleware(InspectAfter)
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
        "headers": [(b"content-type", b"application/json")],
    }
    received = False

    async def receive():
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": b'{"a":1}', "more_body": False}
        await asyncio.Event().wait()

    async def send(message):
        pass

    try:
        await asyncio.wait_for(app(scope, receive, send), 2)
    except TimeoutError:
        raise AssertionError("middleware could not read body after call_next") from None


asyncio.run(main())
