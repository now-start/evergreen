from unittest.mock import Mock

import pytest

from evergreen import main as main_module
from evergreen.platform.config import PlatformSettings


def test_main_starts_uvicorn_with_configured_address(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = PlatformSettings(
        server_address="127.0.0.1",
        server_port=18080,
        management_server_port=18081,
    )
    run_server = Mock()
    monkeypatch.setattr(main_module, "bootstrap_platform", lambda: settings)
    monkeypatch.setattr(main_module, "run_server", run_server)

    main_module.main()

    run_server.assert_called_once_with(settings)


def test_main_exits_cleanly_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = PlatformSettings()
    monkeypatch.setattr(main_module, "bootstrap_platform", lambda: settings)
    monkeypatch.setattr(main_module, "run_server", Mock(side_effect=KeyboardInterrupt))

    main_module.main()


def test_run_server_binds_application_and_management_ports(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = PlatformSettings(
        server_address="127.0.0.1",
        server_port=18080,
        management_server_port=18081,
    )
    application_socket = Mock()
    management_socket = Mock()
    application_config = Mock()
    application_config.bind_socket.return_value = application_socket
    management_config = Mock()
    management_config.bind_socket.return_value = management_socket
    config_factory = Mock(side_effect=[application_config, management_config])
    server = Mock()
    server_factory = Mock(return_value=server)
    monkeypatch.setattr("evergreen.main.uvicorn.Config", config_factory)
    monkeypatch.setattr("evergreen.main.uvicorn.Server", server_factory)

    main_module.run_server(settings)

    assert config_factory.call_args_list == [
        (("evergreen.api:app",), {"host": "127.0.0.1", "port": 18080}),
        (("evergreen.api:app",), {"host": "127.0.0.1", "port": 18081}),
    ]
    server_factory.assert_called_once_with(application_config)
    server.run.assert_called_once_with(sockets=[application_socket, management_socket])
    application_socket.close.assert_called_once_with()
    management_socket.close.assert_called_once_with()
