from starlette.routing import Route


class Endpoint:
    async def my_method(self, request):
        return None


assert Route("/method", Endpoint().my_method).name == "my_method"
