import tomllib
from importlib.metadata import version
from pathlib import Path


def test_package_is_installed() -> None:
    project_file = Path(__file__).resolve().parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]
    assert version("evergreen") == project["version"]
