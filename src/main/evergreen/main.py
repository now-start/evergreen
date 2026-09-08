import logging
import socket

import uvicorn

from evergreen.observability import operation
from evergreen.platform.bootstrap import bootstrap_platform
from evergreen.platform.config import PlatformSettings

logger = logging.getLogger(__name__)


def run_server(settings: PlatformSettings) -> None:
    config = uvicorn.Config(
        "evergreen.api:app",
        host=settings.server_address,
        port=settings.server_port,
    )
    sockets: list[socket.socket] = []
    try:
        sockets.append(config.bind_socket())
        if settings.management_server_port != settings.server_port:
            management_config = uvicorn.Config(
                "evergreen.api:app",
                host=settings.server_address,
                port=settings.management_server_port,
            )
            sockets.append(management_config.bind_socket())
        logger.info(
            "event=listeners_bound api_port=%d management_port=%d",
            settings.server_port,
            settings.management_server_port,
        )
        with operation(logger, "http_server", level=logging.INFO):
            uvicorn.Server(config).run(sockets=sockets)
    finally:
        for bound_socket in sockets:
            bound_socket.close()
        logger.info("event=listeners_closed")


def main() -> None:
    settings = bootstrap_platform()
    try:
        run_server(settings)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
