from starlette import status

assert status.WS_1005_NO_STATUS_RCVD == 1005, "WebSocket close code 1005 is incorrect"
assert status.WS_1006_ABNORMAL_CLOSURE == 1006
assert status.WS_1004_NO_STATUS_RCVD == 1004
assert status.WS_1005_ABNORMAL_CLOSURE == 1005
