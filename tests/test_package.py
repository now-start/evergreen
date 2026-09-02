from importlib.metadata import version


def test_package_is_installed() -> None:
    assert version("evergreen") == "0.1.0"
