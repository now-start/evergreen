from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version

from fastapi import FastAPI

from evergreen.platform.discovery import deregister_from_eureka, register_with_eureka
from evergreen.platform.management import configure_management
from evergreen.platform.settings import PlatformSettings, get_settings


def create_app(settings: PlatformSettings) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        eureka_client = await register_with_eureka(settings)
        try:
            yield
        finally:
            await deregister_from_eureka(eureka_client)

    app = FastAPI(
        title=f"{settings.spring_application_name} API",
        description=f"{settings.spring_application_name} service API",
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
    configure_management(app, settings)
    return app


app = create_app(get_settings())
