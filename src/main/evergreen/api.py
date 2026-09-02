from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI
from pyctuator.pyctuator import Pyctuator

from evergreen.discovery import deregister_from_eureka, register_with_eureka
from evergreen.port_routing import PlatformPortRoutingMiddleware
from evergreen.settings import PlatformSettings, get_settings


def create_app(settings: PlatformSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        eureka_client = await register_with_eureka(settings)
        try:
            yield
        finally:
            await deregister_from_eureka(eureka_client)

    app = FastAPI(
        title="evergreen API",
        description="evergreen 프로젝트의 API 문서입니다.",
        version=version("evergreen"),
        openapi_url="/v3/api-docs",
        docs_url="/swagger-ui/index.html",
        swagger_ui_oauth2_redirect_url="/swagger-ui/oauth2-redirect.html",
        redoc_url=None,
        servers=[
            {
                "url": f"/{settings.spring_application_name.lower()}",
                "description": "Platform Gateway",
            },
            {"url": "/", "description": "Direct"},
        ],
        lifespan=lifespan,
    )
    app.add_middleware(
        PlatformPortRoutingMiddleware,
        application_port=settings.server_port,
        management_port=settings.management_server_port,
    )
    app.state.actuator = Pyctuator(
        app=app,
        app_name=settings.spring_application_name,
        app_description="Chart-driven Bitcoin automated trading system",
        app_url="/",
        pyctuator_endpoint_url="/actuator",
        registration_url=None,
    )
    return app


app = create_app(get_settings())
