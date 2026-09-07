import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from evergreen.market import Candle
from evergreen.research.backtest import Costs, Result, run_backtest
from evergreen.strategies import Strategy


def test_fixed_breakout_assessment_rejects_no_trades_and_losses() -> None:
    from evergreen.research.experiments.breakout import assess_breakout

    start = datetime(2026, 7, 24, tzinfo=UTC)
    bars = [
        Candle(
            open_time=start + timedelta(hours=i),
            open=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            volume=Decimal(1),
            quote_volume=Decimal(100),
            fetched_at=start + timedelta(days=40),
        )
        for i in range(240)
    ]
    costs = Costs(
        buy_fee=Decimal(".0005"),
        sell_fee=Decimal(".0005"),
        buy_slippage=Decimal(".001"),
        sell_slippage=Decimal(".001"),
        min_notional=Decimal(5000),
        quantity_step=Decimal(".00000001"),
    )
    result = run_backtest(bars, start + timedelta(days=8), Decimal(1000000), costs, "breakout-v1")
    rows = [result.model_copy(deep=True) for _ in range(4)]
    assert assess_breakout(rows) == {"positive": False, "risk": True, "exit_count": False}
    for row in rows:
        row.net_return = Decimal(".01")
        row.natural_exits = 10
    assert all(assess_breakout(rows).values())
    rows[-1].max_drawdown = Decimal(".11")
    rows[-1].net_return = Decimal("-.01")
    assert not assess_breakout(rows)["risk"]
    assert not assess_breakout(rows)["positive"]
    with pytest.raises(ValueError):
        assess_breakout([])


def test_breakout_cli_dispatch(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from evergreen.research.__main__ import main

    def run(dataset: Path, origin: Path, output: Path) -> dict[str, bool]:
        assert dataset == Path("data") and origin == Path("origin") and output == tmp_path
        return {"positive": False, "risk": True, "exit_count": False}

    monkeypatch.setattr("evergreen.research.experiments.breakout.run_breakout_study", run)
    assert (
        main(
            [
                "breakout-study",
                "--dataset",
                "data",
                "--trained-experiment",
                "origin",
                "--output",
                str(tmp_path),
            ]
        )
        == 0
    )


@pytest.mark.parametrize("failure", [None, "quality", "interval", "comparison"])
def test_breakout_protocol_precedes_data_and_failures_never_pass(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str | None
) -> None:
    from evergreen.research.experiments.breakout import run_breakout_study

    output = tmp_path / "run"
    start = datetime(2026, 7, 24, tzinfo=UTC)
    candles = [
        Candle(
            open_time=start + timedelta(hours=i),
            open=Decimal(100),
            high=Decimal(100),
            low=Decimal(100),
            close=Decimal(100),
            volume=Decimal(1),
            quote_volume=Decimal(100),
            fetched_at=start + timedelta(days=40),
        )
        for i in range(936)
    ]

    def read(path: Path) -> tuple[list[Candle], str]:
        protocol = json.loads((output / "protocol.json").read_text())
        assert protocol["selected"] == "breakout-v1"
        assert protocol["interval"] == ["2026-07-24", "2026-08-01", "2026-09-01"]
        assert "max_hold_hours" not in protocol["strategy_parameters"]
        assert not protocol["live_enabled"]
        assert not (output / "baseline").exists()
        if failure == "quality":
            raise ValueError("품질 실패")
        return candles[:-1] if failure == "interval" else candles, "a" * 64

    scores = {bar.close_time: Decimal(0) for bar in candles[191:]}

    def comparison(
        dataset: Path,
        start: datetime,
        capital: Decimal,
        costs: Costs,
        output: Path,
        *,
        strategies: tuple[Strategy, ...],
        predictions: dict[Strategy, dict[datetime, Decimal]],
    ) -> list[Result]:
        assert strategies == ("breakout-v1", "cash", "buy-hold", "sma-slow-v1", "mlp-v1")
        assert set(predictions) == {"mlp-v1"}
        if failure == "comparison":
            raise ValueError("비교 실패")
        return [
            run_backtest(candles, start, capital, costs, name, predictions=predictions.get(name))
            for _ in range(4)
            for name in strategies
        ]

    monkeypatch.setattr("evergreen.research.experiments.breakout.load_dataset", read)
    monkeypatch.setattr(
        "evergreen.research.experiments.breakout.frozen_models", lambda *args: {"mlp-v1": object()}
    )
    monkeypatch.setattr(
        "evergreen.research.experiments.breakout._prediction_artifacts",
        lambda *args: {"mlp-v1": scores},
    )
    monkeypatch.setattr("evergreen.research.experiments.breakout.compare", comparison)
    if failure:
        with pytest.raises(ValueError):
            run_breakout_study(Path("data"), Path("origin"), output)
        assert json.loads((output / "failure.json").read_text())["status"] == "failed"
        assert not (output / "status.json").exists()
        assert "검증 실패" in (output / "summary.md").read_text()
    else:
        checks = run_breakout_study(Path("data"), Path("origin"), output)
        assert checks == {"positive": False, "risk": True, "exit_count": False}
        status = json.loads((output / "status.json").read_text())
        assert status["status"] == "completed"
        assert not status["research_checks_passed"] and not status["live_enabled"]
        assert "미달" in (output / "summary.md").read_text()
        with pytest.raises(FileExistsError):
            run_breakout_study(Path("data"), Path("origin"), output)
