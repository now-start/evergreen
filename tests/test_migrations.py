from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from alembic import command
from alembic.script import ScriptDirectory


def test_packaged_migration_has_single_head_and_frozen_schema() -> None:
    from evergreen.database.migration import configuration

    scripts = ScriptDirectory.from_config(configuration())
    assert scripts.get_heads() == ["0001_execution"]
    revision = scripts.get_revision("head")
    assert revision is not None and revision.down_revision is None
    source = Path(revision.path).read_text()
    assert "evergreen.trading" not in source
    assert "create_all" not in source


def test_history_does_not_load_config_or_access_database(monkeypatch: pytest.MonkeyPatch) -> None:
    from evergreen.database.__main__ import main

    def fail(*args: object, **kwargs: object) -> None:
        raise AssertionError("must not access Config Server")

    monkeypatch.setattr("evergreen.database.__main__.load_spring_config", fail)
    assert main(["history"]) == 0


def test_local_boot_skips_database_and_production_migrates(monkeypatch: pytest.MonkeyPatch) -> None:
    from evergreen.database.bootstrap import initialize_database
    from evergreen.platform.config import PlatformSettings

    migrate = AsyncMock()
    monkeypatch.setattr("evergreen.database.bootstrap.run_migrations", migrate)
    initialize_database(PlatformSettings(spring_profiles_active="local"))
    migrate.assert_not_called()
    initialize_database(PlatformSettings())
    migrate.assert_awaited_once()


def test_migration_failure_prevents_socket_binding_and_redacts_error(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from evergreen import main as main_module
    from evergreen.database.bootstrap import initialize_database
    from evergreen.platform.config import PlatformSettings

    migrate = AsyncMock(side_effect=RuntimeError("secret-database-value"))
    server = Mock()
    monkeypatch.setattr("evergreen.database.bootstrap.run_migrations", migrate)
    monkeypatch.setattr(
        main_module, "bootstrap_platform", lambda: initialize_database(PlatformSettings())
    )
    monkeypatch.setattr(main_module, "run_server", server)
    with pytest.raises(RuntimeError, match="서비스 기동 중단") as failure:
        main_module.main()
    server.assert_not_called()
    assert "secret-database-value" not in str(failure.value)
    assert "event=database_migration status=failed error_type=RuntimeError" in caplog.text
    assert "secret-database-value" not in caplog.text


def test_database_cli_failure_redacts_details(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from evergreen.database.__main__ import main

    monkeypatch.setattr("evergreen.database.__main__.load_dotenv", Mock())
    monkeypatch.setattr("evergreen.database.__main__.load_spring_config", Mock())
    monkeypatch.setattr(
        "evergreen.database.__main__.run_migrations", AsyncMock(side_effect=ValueError("secret"))
    )
    assert main(["upgrade"]) == 1
    assert "secret" not in capsys.readouterr().err


def test_direct_alembic_upgrade_cannot_bypass_connection_lock() -> None:
    from evergreen.database.migration import configuration

    with pytest.raises(RuntimeError, match=r"evergreen\.database"):
        command.upgrade(configuration(), "head")
