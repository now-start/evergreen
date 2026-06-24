from __future__ import annotations

import importlib
import re
from dataclasses import asdict, dataclass, fields, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from evergreen_backtest.contracts import CONTRACT_SCHEMA_VERSION, evaluation_from_row, evaluation_to_dict
from evergreen_backtest.data import CandleBar


STRATEGY_DIR = Path(__file__).resolve().parent / "strategies"
STRATEGY_PACKAGE = "evergreen_backtest.strategies"
VERSION_FILE = re.compile(r"^v(?P<number>\d+)\.py$")
JAVA_PARAM_FIELDS = {
    "v1": {"rsiBuy", "maLen", "maSlopeDays"},
    "v2": {"regimeEmaLen", "atrPeriod", "atrTrailMultiplier", "regimeBand"},
    "v3": {
        "regimeEmaLen",
        "atrPeriod",
        "atrTrailMultiplier",
        "regimeBand",
        "volTarget",
        "maxLeverage",
        "minExposure",
    },
    "v4": {"regimeEmaLen", "atrPeriod", "atrTrailMultiplier", "regimeBand", "weeklyEmaLen"},
    "v5": {
        "regimeEmaLen",
        "atrPeriod",
        "atrMultLowVol",
        "atrMultHighVol",
        "volRegimeLookback",
        "volRegimeThreshold",
        "regimeBand",
    },
}


@dataclass(frozen=True)
class VersionAdapter:
    name: str
    module: ModuleType
    config_class: type[Any]
    candle_class: type[Any]
    service_class: type[Any]
    grid_search_class: type[Any]

    @property
    def number(self) -> int:
        return int(self.name[1:])

    @property
    def config_field_names(self) -> set[str]:
        return {field.name for field in fields(self.config_class)}

    def config(self, overrides: dict[str, Any]) -> Any:
        allowed = self.config_field_names
        unknown = sorted(set(overrides) - allowed)
        if unknown:
            raise ValueError(f"{self.name} does not support config overrides: {', '.join(unknown)}")
        return self.config_class(**overrides)

    def convert_bars(self, bars: list[CandleBar]) -> list[Any]:
        return [
            self.candle_class(
                timestamp=bar.timestamp,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
            for bar in bars
        ]

    def run(self, bars: list[CandleBar], overrides: dict[str, Any]) -> "VersionRun":
        config = self.config(overrides)
        if not config.enabled:
            raise ValueError(f"{self.name} is disabled by config")

        strategy_bars = self.convert_bars(bars)
        split_index = config.validation_split_index(len(strategy_bars))
        validation_bars = strategy_bars[:split_index]
        test_bars = strategy_bars[split_index:]

        service = self.service_class()
        grid_search = self.grid_search_class(service)
        candidates = grid_search.search(validation_bars, config)
        if not candidates:
            raise RuntimeError(f"{self.name} grid search returned no candidates")

        selected_params = candidates[0].params
        validation_result = service.backtest_daily_strategy(validation_bars, selected_params)
        test_result = service.backtest_daily_strategy(test_bars, selected_params)
        full_result = service.backtest_daily_strategy(strategy_bars, selected_params)

        return VersionRun(
            version=self.name,
            config=config,
            selected_params=selected_params,
            candidates=candidates,
            validation_result=validation_result,
            test_result=test_result,
            full_result=full_result,
        )


@dataclass(frozen=True)
class VersionRun:
    version: str
    config: Any
    selected_params: Any
    candidates: list[Any]
    validation_result: Any
    test_result: Any
    full_result: Any

    def summary_rows(self) -> list[dict[str, Any]]:
        return [
            self._summary_row("validation", self.validation_result.summary),
            self._summary_row("test", self.test_result.summary),
            self._summary_row("full", self.full_result.summary),
        ]

    def contract(self) -> dict[str, Any]:
        params = to_plain_dict(self.selected_params)
        params_camel = {snake_to_camel(key): value for key, value in params.items()}
        java_params = {key: value for key, value in params_camel.items() if key in JAVA_PARAM_FIELDS[self.version]}
        return {
            "version": self.version,
            "ioContractSchemaVersion": CONTRACT_SCHEMA_VERSION,
            "javaInteropReady": True,
            "pythonParameterClass": type(self.selected_params).__name__,
            "javaParamsCamelCase": java_params,
            "selectedParams": params,
            "selectedParamsCamelCase": params_camel,
            "lastEvaluation": evaluation_to_dict(evaluation_from_row(self.full_result.rows[-1])),
            "validation": summary_to_dict(self.validation_result.summary),
            "test": summary_to_dict(self.test_result.summary),
            "full": summary_to_dict(self.full_result.summary),
        }

    def _summary_row(self, phase: str, summary: Any) -> dict[str, Any]:
        row = summary_to_dict(summary)
        row["version"] = self.version
        row["phase"] = phase
        return row


def available_versions() -> list[str]:
    return [adapter.name for adapter in discover_versions()]


def get_version(name: str) -> VersionAdapter:
    normalized = name.strip().lower()
    for adapter in discover_versions():
        if adapter.name == normalized:
            return adapter
    raise KeyError(f"unknown backtest version: {name}")


def discover_versions() -> list[VersionAdapter]:
    adapters = [_adapter_from_module(path.stem) for path in STRATEGY_DIR.glob("v*.py") if VERSION_FILE.match(path.name)]
    adapters.sort(key=lambda adapter: adapter.number)
    return adapters


def to_plain_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return {key: _to_json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    return {"value": _to_json_value(value)}


def summary_to_dict(summary: Any) -> dict[str, Any]:
    return to_plain_dict(summary)


def snake_to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _adapter_from_module(module_name: str) -> VersionAdapter:
    module = importlib.import_module(f"{STRATEGY_PACKAGE}.{module_name}")
    version_suffix = module_name[1:]
    return VersionAdapter(
        name=module_name,
        module=module,
        config_class=getattr(module, f"BacktestConfigV{version_suffix}"),
        candle_class=getattr(module, "CandleBar"),
        service_class=getattr(module, f"BacktestServiceV{version_suffix}"),
        grid_search_class=getattr(module, f"GridSearchServiceV{version_suffix}"),
    )


def _to_json_value(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if is_dataclass(value):
        return to_plain_dict(value)
    if isinstance(value, (list, tuple)):
        return [_to_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    return value
