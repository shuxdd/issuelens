import functools

from starlette.applications import Starlette
from starlette.routing import WebSocketRoute
from starlette.testclient import TestClient


async def endpoint(websocket):
    await websocket.accept()
    await websocket.send_text("partial websocket works")
    await websocket.close()


app = Starlette(routes=[WebSocketRoute("/ws", functools.partial(endpoint))])
with TestClient(app) as client, client.websocket_connect("/ws") as websocket:
    assert websocket.receive_text() == "partial websocket works"
