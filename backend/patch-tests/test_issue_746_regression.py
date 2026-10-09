from starlette.routing import Mount, Route
from starlette.schemas import SchemaGenerator


async def endpoint(request):
    pass


generator = SchemaGenerator({})
plain = generator.get_endpoints([Route("/health", endpoint)])
assert plain[0].path == "/health"
mounted = generator.get_endpoints([Mount("/api", routes=[Route("/users/{id:int}", endpoint)])])
assert mounted[0].path == "/api/users/{id}"
