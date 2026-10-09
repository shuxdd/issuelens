from starlette.responses import RedirectResponse, Response

assert RedirectResponse("/").headers["content-length"] == "0"
assert Response().headers["content-length"] == "0"
assert Response("hi").headers["content-length"] == "2"
