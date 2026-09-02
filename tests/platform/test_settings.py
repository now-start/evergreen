from evergreen.platform.settings import PlatformSettings


def test_default_profile_enables_platform_integrations() -> None:
    settings = PlatformSettings()

    assert settings.active_profiles == {"default"}
    assert settings.platform_integrations_enabled is True
    assert settings.server_port == 8080
    assert settings.management_server_port == 8081


def test_local_profile_disables_platform_integrations() -> None:
    settings = PlatformSettings(spring_profiles_active="default, local")

    assert settings.platform_integrations_enabled is False
