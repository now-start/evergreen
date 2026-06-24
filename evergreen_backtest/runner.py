from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from evergreen_backtest.contracts import CONTRACT_SCHEMA_VERSION, strategy_io_contract
from evergreen_backtest.data import CandleBar, DailyCandleClient, load_bars
from evergreen_backtest.versions import VersionAdapter, VersionRun, available_versions, get_version


@dataclass(frozen=True)
class BacktestRunRequest:
    versions: tuple[str, ...] = ("all",)
    market: str = "KRW-BTC"
    from_dt: datetime = datetime(2020, 1, 1, tzinfo=timezone.utc)
    to_dt: datetime | None = None
    cache_dir: str | Path = "outputs/data/upbit-cache"
    common_config: dict[str, Any] = field(default_factory=dict)
    version_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    output_dir: str | Path = "outputs/backtests/latest"

    def resolved_versions(self) -> tuple[str, ...]:
        versions = tuple(version.strip().lower() for version in self.versions)
        if versions == ("all",):
            return tuple(available_versions())
        return versions


@dataclass(frozen=True)
class ExperimentResult:
    request: BacktestRunRequest
    bars: list[CandleBar]
    runs: dict[str, VersionRun]

    def summary_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for version in self.request.resolved_versions():
            rows.extend(self.runs[version].summary_rows())
        return rows

    def summary_dataframe(self) -> Any:
        import pandas as pd

        return pd.DataFrame(self.summary_rows())

    def contracts(self) -> dict[str, Any]:
        return {
            "contractSchemaVersion": CONTRACT_SCHEMA_VERSION,
            "strategyIoContract": strategy_io_contract(),
            "market": self.request.market,
            "dataProvider": "upbit",
            "barCount": len(self.bars),
            "from": self.bars[0].timestamp.isoformat() if self.bars else None,
            "to": self.bars[-1].timestamp.isoformat() if self.bars else None,
            "versions": {version: run.contract() for version, run in self.runs.items()},
        }

    def write_outputs(self, output_dir: str | Path | None = None) -> Path:
        out_dir = Path(output_dir or self.request.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        self._write_summary_csv(out_dir / "summary.csv")
        (out_dir / "strategy_contracts.json").write_text(
            json.dumps(self.contracts(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return out_dir

    def _write_summary_csv(self, path: Path) -> None:
        rows = self.summary_rows()
        if not rows:
            return
        fieldnames = ["version", "phase", *[key for key in rows[0] if key not in {"version", "phase"}]]
        with path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.DictWriter(fp, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)


def run_backtest(
    request: BacktestRunRequest | None = None,
    *,
    client: DailyCandleClient | None = None,
) -> ExperimentResult:
    req = request or BacktestRunRequest()
    bars = load_bars(
        market=req.market,
        from_dt=req.from_dt,
        to_dt=req.to_dt,
        cache_dir=req.cache_dir,
        client=client,
    )

    runs: dict[str, VersionRun] = {}
    for version in req.resolved_versions():
        adapter = get_version(version)
        overrides = _config_for_version(req, adapter)
        runs[version] = adapter.run(bars, overrides)
    return ExperimentResult(request=req, bars=bars, runs=runs)


def _config_for_version(request: BacktestRunRequest, adapter: VersionAdapter) -> dict[str, Any]:
    supported = adapter.config_field_names
    overrides = {key: value for key, value in request.common_config.items() if key in supported}

    version_overrides = request.version_config.get(adapter.name, {})
    unknown = sorted(set(version_overrides) - supported)
    if unknown:
        raise ValueError(
            f"{adapter.name} does not support version_config overrides: {', '.join(unknown)}"
        )
    overrides.update(version_overrides)
    return overrides
