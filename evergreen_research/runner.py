from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from evergreen_research.contracts import CONTRACT_SCHEMA_VERSION, strategy_io_contract
from evergreen_research.data import CandleBar, CandleClient, load_bars, normalize_interval_key
from evergreen_research.versions import VersionAdapter, VersionRun, available_versions, get_version
from evergreen_research.walk_forward import WalkForwardConfig, WalkForwardResult, WalkForwardSelector


@dataclass(frozen=True)
class BacktestRunRequest:
    versions: tuple[str, ...] = ("all",)
    market: str = "KRW-BTC"
    interval_key: str = "auto"
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
    interval_key: str
    bars: list[CandleBar]
    bars_by_interval: dict[str, list[CandleBar]]
    interval_by_version: dict[str, str]
    runs: dict[str, VersionRun]
    walk_forward: WalkForwardResult | None = None
    walk_forward_by_version: dict[str, WalkForwardResult] = field(default_factory=dict)

    def summary_rows(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.walk_forward_by_version:
            for version in self.request.resolved_versions():
                if version not in self.walk_forward_by_version:
                    continue
                row = self.walk_forward_by_version[version].summary_row(
                    phase="walk_forward_version",
                    version=version,
                    selection_mode="fixed_version",
                )
                row["interval_key"] = self.interval_by_version[version]
                rows.append(row)
            return rows

        for version in self.request.resolved_versions():
            for row in self.runs[version].summary_rows():
                row["interval_key"] = self.interval_by_version[version]
                rows.append(row)
        return rows

    def summary_dataframe(self) -> Any:
        import pandas as pd

        df = pd.DataFrame(self.summary_rows())
        columns = [column for column in _SUMMARY_FIELD_ORDER if column in df.columns]
        columns.extend(column for column in df.columns if column not in columns)
        return df[columns]

    def contracts(self) -> dict[str, Any]:
        version_contracts = {}
        for version, run in self.runs.items():
            contract = run.contract()
            contract["intervalKey"] = self.interval_by_version[version]
            version_contracts[version] = contract

        return {
            "contractSchemaVersion": CONTRACT_SCHEMA_VERSION,
            "strategyIoContract": strategy_io_contract(),
            "market": self.request.market,
            "dataProvider": "upbit",
            "intervalKey": self.interval_key,
            "barCount": _total_bar_count(self.bars_by_interval),
            "from": _first_timestamp(self.bars_by_interval),
            "to": _last_timestamp(self.bars_by_interval),
            "intervals": {
                interval: {
                    "barCount": len(bars),
                    "from": bars[0].timestamp.isoformat() if bars else None,
                    "to": bars[-1].timestamp.isoformat() if bars else None,
                }
                for interval, bars in self.bars_by_interval.items()
            },
            "versionIntervals": self.interval_by_version,
            "versions": version_contracts,
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
    client: CandleClient | None = None,
) -> ExperimentResult:
    req = request or BacktestRunRequest()
    adapters = [get_version(version) for version in req.resolved_versions()]
    interval_by_version = _resolve_interval_by_version(req.interval_key, adapters)
    interval_order = _ordered_unique(interval_by_version[adapter.name] for adapter in adapters)
    bars_by_interval = {
        interval: load_bars(
            market=req.market,
            from_dt=req.from_dt,
            to_dt=req.to_dt,
            cache_dir=req.cache_dir,
            interval_key=interval,
            client=client,
        )
        for interval in interval_order
    }
    interval_key = interval_order[0] if len(interval_order) == 1 else "mixed"
    bars = bars_by_interval[interval_key] if interval_key != "mixed" else []

    runs: dict[str, VersionRun] = {}
    configs: dict[str, Any] = {}
    for adapter in adapters:
        overrides = _config_for_version(req, adapter)
        config = adapter.config(overrides)
        configs[adapter.name] = config
        runs[adapter.name] = adapter.run(bars_by_interval[interval_by_version[adapter.name]], overrides)

    walk_forward = None
    walk_forward_by_version = {}
    if req.walk_forward_config.enabled:
        selector = WalkForwardSelector()
        selected_results: list[WalkForwardResult] = []
        for interval in interval_order:
            interval_adapters = [
                adapter
                for adapter in adapters
                if interval_by_version[adapter.name] == interval
            ]
            walk_forward_runs = selector.run_set(
                bars=bars_by_interval[interval],
                adapters=interval_adapters,
                configs={adapter.name: configs[adapter.name] for adapter in interval_adapters},
                config=req.walk_forward_config,
            )
            selected_results.append(walk_forward_runs.selected)
            walk_forward_by_version.update(walk_forward_runs.by_version)
        walk_forward = _select_top_walk_forward(selected_results)
    return ExperimentResult(
        request=req,
        interval_key=interval_key,
        bars=bars,
        bars_by_interval=bars_by_interval,
        interval_by_version=interval_by_version,
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


def _resolve_interval_by_version(requested: str, adapters: list[VersionAdapter]) -> dict[str, str]:
    if requested.strip().lower() != "auto":
        interval_key = normalize_interval_key(requested)
        return {adapter.name: interval_key for adapter in adapters}
    return {
        adapter.name: normalize_interval_key(adapter.preferred_interval_key)
        for adapter in adapters
    }


def _ordered_unique(values: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def _select_top_walk_forward(results: list[WalkForwardResult]) -> WalkForwardResult | None:
    if not results:
        return None
    return max(results, key=lambda result: result.summary.final_equity)


def _total_bar_count(bars_by_interval: dict[str, list[CandleBar]]) -> int:
    return sum(len(bars) for bars in bars_by_interval.values())


def _first_timestamp(bars_by_interval: dict[str, list[CandleBar]]) -> str | None:
    timestamps = [
        bars[0].timestamp
        for bars in bars_by_interval.values()
        if bars
    ]
    return min(timestamps).isoformat() if timestamps else None


def _last_timestamp(bars_by_interval: dict[str, list[CandleBar]]) -> str | None:
    timestamps = [
        bars[-1].timestamp
        for bars in bars_by_interval.values()
        if bars
    ]
    return max(timestamps).isoformat() if timestamps else None


_SUMMARY_FIELD_ORDER = [
    "version",
    "phase",
    "interval_key",
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
