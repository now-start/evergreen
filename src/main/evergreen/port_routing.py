from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send


class PlatformPortRoutingMiddleware:
    """Separate application and management routes like Spring Boot."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        application_port: int,
        management_port: int,
    ) -> None:
        self.app = app
        self.application_port = application_port
        self.management_port = management_port

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        server = scope.get("server")
        if server is None or self.application_port == self.management_port:
            await self.app(scope, receive, send)
            return

        request_port = server[1]
        is_management_path = scope["path"].startswith("/actuator")
        allowed = (request_port == self.management_port and is_management_path) or (
            request_port == self.application_port and not is_management_path
        )

        if allowed:
            await self.app(scope, receive, send)
            return

        await Response(status_code=404)(scope, receive, send)
