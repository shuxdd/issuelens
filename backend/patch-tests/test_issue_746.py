from starlette.routing import Route
from starlette.schemas import SchemaGenerator


async def user(request):
    pass


endpoints = SchemaGenerator({}).get_endpoints([Route("/users/{id:int}", user)])
assert endpoints[0].path == "/users/{id}", "OpenAPI path retained Starlette converter"
