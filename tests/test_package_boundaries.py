import importlib
import subprocess
import sys


def test_live_signal_import_does_not_load_research_or_torch() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import sys; import evergreen.strategies; import evergreen.market; "
            "assert not any(k.startswith('evergreen.research') for k in sys.modules); "
            "assert 'torch' not in sys.modules",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_experiment_and_learning_modules_are_grouped() -> None:
    for name in ("rules", "deep", "early_stopping", "hybrid", "meta", "breakout"):
        importlib.import_module(f"evergreen.research.experiments.{name}")
    for name in ("models", "meta"):
        importlib.import_module(f"evergreen.research.learning.{name}")
