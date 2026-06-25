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
from evergreen_backtest.walk_forward import WalkForwardConfig, WalkForwardResult, WalkForwardSelector


@dataclass(frozen=True)
class BacktestRunRequest:
    versions: tuple[str, ...] = ("all",)
    market: str = "KRW-BTC"
    from_dt: datetime = datetime(2020, 1, 1, tzinfo=timezone.utc)
    to_dt: datetime | None = None
    cache_dir: str | Path = "outputs/data/upbit-cache"
    common_config: dict[str, Any] = field(default_factory=dict)
    version_config: dict[str, dict[str, Any]] = field(default_factory=dict)
    walk_forward_config: WalkForwardConfig = field(default_factory=WalkForwardConfig)
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
    walk_forward: WalkForwardResult | None = None
    walk_forward_by_version: dict[str, WalkForwardResult] = field(default_factory=dict)

    def summary_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.walk_forward is not None:
            for version in self.request.resolved_versions():
                if version not in self.walk_forward_by_version:
                    continue
                rows.append(
                    self.walk_forward_by_version[version].summary_row(
                        phase="walk_forward_version",
                        version=version,
                        selection_mode="fixed_version",
                    )
                )
            return rows

        for version in self.request.resolved_versions():
            rows.extend(self.runs[version].summary_rows())
        return rows

    def summary_dataframe(self) -> Any:
        import pandas as pd

        df = pd.DataFrame(self.summary_rows())
        columns = [column for column in _SUMMARY_FIELD_ORDER if column in df.columns]
        columns.extend(column for column in df.columns if column not in columns)
        return df[columns]

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
            "walkForwardByVersion": {
                version: result.contract({version: run.adapter for version, run in self.runs.items()})
                for version, result in self.walk_forward_by_version.items()
            },
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
        all_fields = {key for row in rows for key in row}
        fieldnames = [field for field in _SUMMARY_FIELD_ORDER if field in all_fields]
        fieldnames.extend(sorted(all_fields - set(fieldnames)))
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

    adapters = [get_version(version) for version in req.resolved_versions()]
    runs: dict[str, VersionRun] = {}
    configs: dict[str, Any] = {}
    for adapter in adapters:
        overrides = _config_for_version(req, adapter)
        config = adapter.config(overrides)
        configs[adapter.name] = config
        runs[adapter.name] = adapter.run(bars, overrides)

    walk_forward = None
    walk_forward_by_version = {}
    if req.walk_forward_config.enabled:
        walk_forward_runs = WalkForwardSelector().run_set(
            bars=bars,
            adapters=adapters,
            configs=configs,
            config=req.walk_forward_config,
        )
        walk_forward = walk_forward_runs.selected
        walk_forward_by_version = walk_forward_runs.by_version
    return ExperimentResult(
        request=req,
        bars=bars,
        runs=runs,
        walk_forward=walk_forward,
        walk_forward_by_version=walk_forward_by_version,
    )


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


_SUMMARY_FIELD_ORDER = [
    "version",
    "phase",
    "selection_mode",
    "current_version",
    "final_equity",
    "final_equity_bh",
    "cagr",
    "mdd",
    "trades",
    "range",
    "window_count",
]
