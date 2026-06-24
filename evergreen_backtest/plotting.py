from __future__ import annotations

from pathlib import Path
from typing import Any

from evergreen_backtest.runner import ExperimentResult


def plot_equity_curves(result: ExperimentResult, *, phase: str = "test", save_to: str | Path | None = None) -> Any:
    import matplotlib.pyplot as plt

    _configure_korean_font(plt)

    fig, ax = plt.subplots(figsize=(14, 6))
    for version, run in result.runs.items():
        backtest_result = getattr(run, f"{phase}_result")
        timestamps = [row.timestamp for row in backtest_result.rows]
        equity = [row.equity for row in backtest_result.rows]
        ax.plot(timestamps, equity, label=f"{version} 전략")

    first_run = next(iter(result.runs.values()))
    benchmark_result = getattr(first_run, f"{phase}_result")
    ax.plot(
        [row.timestamp for row in benchmark_result.rows],
        [row.equity_bh for row in benchmark_result.rows],
        label="단순 보유",
        linestyle="--",
        color="#5f6368",
    )
    ax.set_title(f"{phase} 구간 자산 곡선")
    ax.set_xlabel("날짜")
    ax.set_ylabel("초기자산 대비 배율")
    ax.grid(alpha=0.2)
    ax.legend()
    fig.tight_layout()

    if save_to is not None:
        path = Path(save_to)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=140, bbox_inches="tight")
    return fig


def plot_walk_forward_equity(result: ExperimentResult, *, save_to: str | Path | None = None) -> Any:
    if result.walk_forward is None:
        raise ValueError("walk-forward result is not available")

    import matplotlib.pyplot as plt

    _configure_korean_font(plt)

    fig, ax = plt.subplots(figsize=(14, 6))
    rows = result.walk_forward.rows
    ax.plot([row.timestamp for row in rows], [row.equity for row in rows], label="워크포워드 선택 모델")
    ax.plot(
        [row.timestamp for row in rows],
        [row.equity_bh for row in rows],
        label="단순 보유",
        linestyle="--",
        color="#5f6368",
    )
    ax.set_title("워크포워드 선택 모델 자산 곡선")
    ax.set_xlabel("날짜")
    ax.set_ylabel("초기자산 대비 배율")
    ax.grid(alpha=0.2)
    ax.legend()
    fig.tight_layout()

    if save_to is not None:
        path = Path(save_to)
        path.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(path, dpi=140, bbox_inches="tight")
    return fig


def _configure_korean_font(plt: Any) -> None:
    from matplotlib import font_manager

    candidates = [
        "Apple SD Gothic Neo",
        "AppleGothic",
        "D2Coding",
        "Arial Unicode MS",
        "NanumGothic",
        "Malgun Gothic",
    ]
    available = {font.name for font in font_manager.fontManager.ttflist}
    plt.rcParams["font.family"] = next((font for font in candidates if font in available), "DejaVu Sans")
    plt.rcParams["axes.unicode_minus"] = False
