import asyncio

from starlette.websockets import WebSocket


async def receive():
    return {"type": "websocket.connect"}


async def send(message):
    pass


socket = WebSocket({"type": "websocket", "path": "/"}, receive, send)
try:
    asyncio.run(socket.send_json({}, mode="invalid"))
except RuntimeError as error:
    assert "mode" in str(error)
else:
    raise AssertionError("invalid WebSocket JSON mode was accepted")
