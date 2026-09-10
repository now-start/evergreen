from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from decimal import Decimal as D
from pathlib import Path

import pytest
from test_breakout_liquidation_buffer import path

from evergreen.market import Candle, write_json
from evergreen.research.experiments import historical_window as runner
from evergreen.research.experiments.breakout_meta import costs
from evergreen.research.experiments.strategy_family import simulate


def source(tmp_path: Path, bars: list[Candle], start: datetime, end: datetime) -> Path:
    root = tmp_path / "raw-input"
    (root / "raw").mkdir(parents=True)
    rows = [
        {
            "market": "KRW-BTC",
            "unit": 60,
            "candle_date_time_utc": b.open_time.isoformat(),
            "opening_price": str(b.open),
            "high_price": str(b.high),
            "low_price": str(b.low),
            "trade_price": str(b.close),
            "candle_acc_trade_volume": str(b.volume),
            "candle_acc_trade_price": str(b.quote_volume),
        }
        for b in bars
    ]
    f = root / "raw/000001.json"
    write_json(f, rows)
    write_json(
        root / "quality.json",
        {
            "status": "passed",
            "start": start.isoformat(),
            "end": end.isoformat(),
            "quality": {},
            "requests": [
                {
                    "raw": f.name,
                    "status": 200,
                    "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
                    "fetched_at": bars[-1].fetched_at.isoformat(),
                }
            ],
        },
    )
    return root


def test_raw_gap_splits_blocks_without_filling(tmp_path: Path) -> None:
    bars = path()
    start, end = bars[0].open_time, bars[-1].close_time
    raw = source(tmp_path, bars[:210] + bars[211:], start, end)
    blocks, quality, hashes = runner.raw_blocks(raw, start, end)
    assert [len(b) for b in blocks] == [210, 49]
    assert quality.missing == [bars[210].open_time]
    assert sum(map(len, blocks)) == 259 and len(hashes) == 2


@pytest.mark.parametrize("damage", ["hash", "http", "range", "conflict", "incomplete-run"])
def test_invalid_raw_sources_fail_closed(tmp_path: Path, damage: str) -> None:
    bars = path()
    start, end = bars[0].open_time, bars[-1].close_time
    if damage == "conflict":
        bars.append(bars[0].model_copy(update={"close": D(99)}))
    raw = source(tmp_path, bars, start, end)
    manifest = json.loads((raw / "quality.json").read_text())
    if damage == "hash":
        manifest["requests"][0]["sha256"] = "bad"
    elif damage == "http":
        manifest["requests"][0]["status"] = 429
    elif damage == "range":
        manifest["end"] = (end + timedelta(hours=1)).isoformat()
    elif damage == "incomplete-run":
        manifest["quality"] = None
        manifest["status"] = "failed"
    (raw / "quality.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        runner.raw_blocks(raw, start, end)


@pytest.mark.parametrize("gap", [False, True])
def test_study_uses_all_post_warmup_hours_and_frozen_strategy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, gap: bool
) -> None:
    bars = path()
    start, end = bars[200].open_time, bars[-1].close_time
    monkeypatch.setattr(runner, "START", start)
    monkeypatch.setattr(runner, "END", end)
    raw = source(tmp_path, bars[:210] + bars[211:] if gap else bars, bars[0].open_time, end)
    output = tmp_path / "output"
    runner.run_study(raw, output)
    coverage = json.loads((output / "coverage.json").read_text())
    assert coverage["evaluated_hours"] == (10 if gap else 60)
    assert coverage["full_period_passed"] is not gap
    assert coverage["accounts"] == 1
    a = json.loads((output / "accounts/block-000/base.liquidation-buffer.json").read_text())
    expected = simulate(bars[:210] if gap else bars, start, "liquidation-buffer", costs(), 0)
    assert a == expected.model_dump(mode="json")
    delayed = json.loads(
        (output / "accounts/block-000/delay-1h.liquidation-buffer.json").read_text()
    )
    assert delayed == simulate(
        bars[:210] if gap else bars, start, "liquidation-buffer", costs(), 1
    ).model_dump(mode="json")
    assert json.loads((output / "status.json").read_text())["pnl_checks"] == 16
    assert not json.loads((output / "status.json").read_text())["live_enabled"]
    with pytest.raises(FileExistsError):
        runner.run_study(raw, output)


def test_no_post_warmup_account_is_not_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bars = path()
    start, end = bars[200].open_time, bars[-1].close_time
    monkeypatch.setattr(runner, "START", start)
    monkeypatch.setattr(runner, "END", end)
    raw = source(tmp_path, bars[:199], bars[0].open_time, end)
    out = tmp_path / "out"
    with pytest.raises(ValueError, match="평가"):
        runner.run_study(raw, out)
    assert (out / "failure.json").exists() and not (out / "status.json").exists()
