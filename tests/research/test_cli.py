import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import httpx
import pytest

from evergreen.market import collect, load_dataset
from evergreen.research.__main__ import main
from evergreen.research.backtest import Costs
from evergreen.research.report import compare

START = datetime(2025, 1, 1, tzinfo=UTC)


def dataset(path: Path) -> None:
    payload = [
        {
            "market": "KRW-BTC",
            "unit": 60,
            "candle_date_time_utc": (START + timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%S"),
            "opening_price": 100 + i,
            "high_price": 100 + i,
            "low_price": 100 + i,
            "trade_price": 100 + i,
            "candle_acc_trade_volume": 1,
            "candle_acc_trade_price": 100 + i,
        }
        for i in reversed(range(64))
    ]
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=payload))
    ) as client:
        collect(client, START, START + timedelta(hours=64), path, sleep=lambda _: None)


def test_cli_generates_three_baselines_and_four_cost_scenarios(tmp_path: Path) -> None:
    source = tmp_path / "data"
    dataset(source)
    output = tmp_path / "report"
    assert (
        main(
            [
                "backtest",
                "--dataset",
                str(source),
                "--start",
                "2025-01-03T12:00:00Z",
                "--capital",
                "1000",
                "--buy-fee",
                "0.001",
                "--sell-fee",
                "0.001",
                "--buy-slippage",
                "0.001",
                "--sell-slippage",
                "0.001",
                "--min-notional",
                "1",
                "--quantity-step",
                "0.00000001",
                "--output",
                str(output),
            ]
        )
        == 0
    )
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["mode"] == "offline"
    assert len(manifest["results"]) == 12
    assert manifest["source_sha256"]
    summary = (output / "summary.md").read_text()
    assert "기존 추세 SMA 20/60" in summary
    assert "순수익률" in summary
    assert "True" not in summary and "False" not in summary
    result = json.loads((output / "base.cash.json").read_text())
    assert Decimal(result["final_equity"]) == 1000


def test_cli_requires_costs() -> None:
    with pytest.raises(SystemExit) as exc:
        main(
            [
                "backtest",
                "--dataset",
                "missing",
                "--start",
                "2025-01-01T00:00:00Z",
                "--capital",
                "1000",
                "--output",
                "unused",
            ]
        )
    assert exc.value.code == 2


def test_loader_detects_dataset_tampering(tmp_path: Path) -> None:
    source = tmp_path / "data"
    dataset(source)
    with (source / "candles.jsonl").open("ab") as stream:
        stream.write(b"\n")
    with pytest.raises(ValueError, match="hash"):
        load_dataset(source)


def test_cli_missing_input_returns_error(tmp_path: Path) -> None:
    assert (
        main(
            [
                "fetch",
                "--start",
                "2025-01-01",
                "--end",
                "2025-01-02",
                "--output",
                str(tmp_path / "bad"),
            ]
        )
        == 1
    )
    assert not (tmp_path / "bad").exists()


def test_compare_does_not_overwrite(tmp_path: Path) -> None:
    source = tmp_path / "data"
    dataset(source)
    cost = Costs(
        buy_fee=Decimal(0),
        sell_fee=Decimal(0),
        buy_slippage=Decimal(0),
        sell_slippage=Decimal(0),
        min_notional=Decimal(1),
        quantity_step=Decimal("0.01"),
    )
    with pytest.raises(FileExistsError):
        compare(source, START + timedelta(hours=60), Decimal(1000), cost, source)


@pytest.mark.parametrize("manifest", [[], {}, {"status": "passed", "sha256": "bad"}])
def test_loader_rejects_invalid_manifest_shape(tmp_path: Path, manifest: object) -> None:
    source = tmp_path / "data"
    dataset(source)
    (source / "quality.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        load_dataset(source)


def test_cli_fetch_without_starting_platform(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evergreen.research import __main__ as cli

    def fake_collect(
        client: httpx.Client, start: datetime, end: datetime, output: Path
    ) -> list[object]:
        assert start == START
        assert end == START + timedelta(hours=1)
        return []

    monkeypatch.setattr(cli, "collect", fake_collect)
    assert (
        main(
            [
                "fetch",
                "--start",
                "2025-01-01T00:00:00Z",
                "--end",
                "2025-01-01T01:00:00Z",
                "--output",
                str(tmp_path / "out"),
            ]
        )
        == 0
    )


def test_compare_keeps_failure_artifact(tmp_path: Path) -> None:
    source = tmp_path / "data"
    dataset(source)
    cost = Costs(
        buy_fee=Decimal("0.6"),
        sell_fee=Decimal(0),
        buy_slippage=Decimal(0),
        sell_slippage=Decimal(0),
        min_notional=Decimal(1),
        quantity_step=Decimal("0.01"),
    )
    output = tmp_path / "out"
    with pytest.raises(ValueError):
        compare(source, START + timedelta(hours=60), Decimal(1000), cost, output)
    assert (output / "failure.json").exists()
    assert not (output / "manifest.json").exists()


def test_loader_checks_metadata_range_after_hash(tmp_path: Path) -> None:
    source = tmp_path / "data"
    dataset(source)
    manifest = json.loads((source / "quality.json").read_text())
    manifest["end"] = (START + timedelta(hours=65)).isoformat()
    (source / "quality.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="quality"):
        load_dataset(source)


@pytest.mark.parametrize("git_present", [False, True])
def test_source_identity_without_repository(
    monkeypatch: pytest.MonkeyPatch, git_present: bool
) -> None:
    from evergreen.research import report

    monkeypatch.setattr(
        "evergreen.research.report.shutil.which", lambda _: "/fake/git" if git_present else None
    )

    def fail(*args: object, **kwargs: object) -> None:
        raise OSError("not a repository")

    monkeypatch.setattr("evergreen.research.report.subprocess.run", fail)
    identity = report.source_identity()
    assert identity["commit"] is None
    assert identity["source_sha256"]
