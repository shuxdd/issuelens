import asyncio
import warnings

from starlette.routing import Router


class StartupHandler:
    def __init__(self):
        self.called = False

    async def __call__(self):
        self.called = True


handler = StartupHandler()
warnings.filterwarnings("ignore", category=RuntimeWarning)
asyncio.run(Router(on_startup=[handler]).startup())
assert handler.called, "async callable startup handler was not awaited"
