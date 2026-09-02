import socket

import uvicorn

from evergreen.bootstrap import bootstrap_platform
from evergreen.settings import PlatformSettings


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
        uvicorn.Server(config).run(sockets=sockets)
    finally:
        for bound_socket in sockets:
            bound_socket.close()


def main() -> None:
    settings = bootstrap_platform()
    try:
        run_server(settings)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":  # pragma: no cover
    main()
