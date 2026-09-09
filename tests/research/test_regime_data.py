import hashlib
import json
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Any

import httpx
import pytest

from evergreen.market import Candle, load_dataset, write_json
from evergreen.research.experiments import regime_data


def test_calendar_intervals_are_fixed() -> None:
    intervals = regime_data.quarters()
    assert len(intervals) == 27
    assert intervals[0][0] == datetime(2020, 1, 1, tzinfo=UTC)
    assert intervals[-1][1] == datetime(2026, 9, 1, tzinfo=UTC)
    assert all(a[1] == b[0] for a, b in pairwise(intervals))


@pytest.mark.parametrize("failure", [None, "hash", "status", "path", "conflict"])
def test_raw_reconstruction_preserves_missing_hours_and_hashes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str | None
) -> None:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(regime_data, "quarters", lambda: [(start, start + timedelta(hours=6))])
    directory = tmp_path / "source" / "2022-01-01"
    (directory / "raw").mkdir(parents=True)
    payload: list[dict[str, Any]] = []
    for i in (0, 1, 3, 4, 5):
        payload.append(
            {
                "market": "KRW-BTC",
                "unit": 60,
                "candle_date_time_utc": (start + timedelta(hours=i)).isoformat(),
                "opening_price": 100,
                "high_price": 100,
                "low_price": 100,
                "trade_price": 100,
                "candle_acc_trade_volume": 1,
                "candle_acc_trade_price": 100,
            }
        )
    if failure == "conflict":
        payload.append(payload[0] | {"candle_acc_trade_volume": 2})
    write_json(directory / "raw" / "000001.json", payload)
    content = (directory / "raw" / "000001.json").read_bytes()
    write_json(
        directory / "quality.json",
        {
            "requests": [
                {
                    "raw": "../bad" if failure == "path" else "000001.json",
                    "status": 403 if failure == "status" else 200,
                    "sha256": "bad" if failure == "hash" else hashlib.sha256(content).hexdigest(),
                    "fetched_at": "2024-01-01T00:00:00Z",
                }
            ]
        },
    )
    output = tmp_path / "blocks"
    if failure:
        with pytest.raises(ValueError):
            regime_data.contiguous_blocks(tmp_path / "source", output)
        return
    blocks = regime_data.contiguous_blocks(tmp_path / "source", output)
    assert [len(block) for block in blocks] == [2, 3]
    assert blocks[1][0].open_time == start + timedelta(hours=3)
    assert len(load_dataset(output / "block-000")[0]) == 2
    coverage = json.loads((output / "coverage.json").read_text())
    assert coverage["full_period_passed"] is False
    assert "2022-01-01T02:00:00Z" in coverage["quality"]["missing"]


@pytest.mark.parametrize("mode", ["pass", "gap", "invalid"])
def test_collection_records_quality_failures_without_hiding_other_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    start = datetime(2022, 1, 1, tzinfo=UTC)
    monkeypatch.setattr(regime_data, "quarters", lambda: [(start, start + timedelta(hours=3))])

    def collect(client: httpx.Client, begin: datetime, end: datetime, output: Path) -> list[Candle]:
        assert begin == start - timedelta(hours=169)
        output.mkdir()
        if mode != "pass":
            write_json(
                output / "quality.json",
                {"quality": {"missing": ["test"] if mode == "gap" else [], "conflicts": []}},
            )
            raise ValueError("test")
        return []

    monkeypatch.setattr(regime_data, "collect", collect)
    monkeypatch.setattr(regime_data, "load_dataset", lambda _: ([], "hash"))
    output = tmp_path / "fetch"
    if mode == "invalid":
        with pytest.raises(ValueError):
            regime_data.collect_quarters(output)
    else:
        regime_data.collect_quarters(output)
        status = json.loads((output / "inventory.json").read_text())[0]["status"]
        assert status == ("passed" if mode == "pass" else "quality_failed")
