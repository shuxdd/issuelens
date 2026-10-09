from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.routing import Mount, Route
from starlette.testclient import TestClient


def fail(request):
    raise RuntimeError("mounted route failed")


class PassThrough(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        return await call_next(request)


sub_app = Starlette(routes=[Route("/", fail)])
app = Starlette(routes=[Mount("/sub", app=sub_app)])
app.add_middleware(PassThrough)

with TestClient(app) as client:
    try:
        client.get("/sub/")
    except RuntimeError as error:
        assert str(error) == "mounted route failed"
    else:
        raise AssertionError("mounted route exception was hidden")
