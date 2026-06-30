from __future__ import annotations

import importlib
import dataclasses
import math
import re
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from evergreen_research.backtest import BacktestEvaluator, BacktestResult
from evergreen_research.contracts import CONTRACT_SCHEMA_VERSION, evaluation_from_row, evaluation_to_dict
from evergreen_research.data import CandleBar
from evergreen_research.optimizer import CandidateResult, HyperparameterOptimizer


MODEL_DIR = Path(__file__).resolve().parent / "models"
MODEL_PACKAGE = "evergreen_research.models"
VERSION_FILE = re.compile(r"^v(?P<number>\d+)\.py$")


@dataclass(frozen=True)
class VersionAdapter:
    name: str
    module: ModuleType
    config_class: type[Any]
    model_class: type[Any]

    @property
    def number(self) -> int:
        return int(self.name[1:])

    @property
    def java_param_fields(self) -> set[str]:
        return set(getattr(self.module, "JAVA_PARAM_FIELDS"))

    @property
    def preferred_interval_key(self) -> str:
        return str(getattr(self.module, "PREFERRED_INTERVAL_KEY", "days"))

    @property
    def config_field_names(self) -> set[str]:
        return {field.name for field in fields(self.config_class)}

    def config(self, overrides: dict[str, Any]) -> Any:
        allowed = self.config_field_names
        unknown = sorted(set(overrides) - allowed)
        if unknown:
            raise ValueError(f"{self.name} does not support config overrides: {', '.join(unknown)}")
        return self.config_class(**overrides)

    def model(self) -> Any:
        return self.model_class()

    def iter_parameter_grid(self, config: Any) -> Any:
        return self.module.iter_parameter_grid(config)

    def resolve_selected_params(self, bars: list[CandleBar], params: Any) -> Any:
        resolver = getattr(self.module, "resolve_selected_params", None)
        if resolver is None:
            return params
        return resolver(bars, params)

    def validation_split_index(self, total_bars: int, config: Any) -> int:
        if total_bars < 4:
            raise ValueError("At least 4 bars are required for validation/test split")
        split = int(math.floor(total_bars * config.validation_ratio))
        split = max(2, split)
        split = min(total_bars - 2, split)
        return split

    def run(self, bars: list[CandleBar], overrides: dict[str, Any]) -> "VersionRun":
        config = self.config(overrides)
        if not config.enabled:
            raise ValueError(f"{self.name} is disabled by config")

        split_index = self.validation_split_index(len(bars), config)
        validation_bars = bars[:split_index]
        test_bars = bars[split_index:]

        model = self.model()
        optimizer = HyperparameterOptimizer()
        candidates = optimizer.search(
            model=model,
            bars=validation_bars,
            params_grid=self.iter_parameter_grid(config),
            top_k=config.top_k,
            parallelism=getattr(config, "grid_parallelism", 1),
        )
        if not candidates:
            raise RuntimeError(f"{self.name} grid search returned no candidates")

        selected_params = self.resolve_selected_params(validation_bars, candidates[0].params)
        evaluator = BacktestEvaluator()
        validation_result = evaluator.evaluate(
            validation_bars,
            model.evaluate(validation_bars, selected_params),
            fee_per_side=selected_params.fee_per_side,
            slippage=selected_params.slippage,
        )
        test_result = evaluator.evaluate(
            test_bars,
            model.evaluate(test_bars, selected_params),
            fee_per_side=selected_params.fee_per_side,
            slippage=selected_params.slippage,
        )
        full_result = evaluator.evaluate(
            bars,
            model.evaluate(bars, selected_params),
            fee_per_side=selected_params.fee_per_side,
            slippage=selected_params.slippage,
        )

        return VersionRun(
            version=self.name,
            adapter=self,
            config=config,
            selected_params=selected_params,
            candidates=candidates,
            validation_result=validation_result,
            test_result=test_result,
            full_result=full_result,
        )

    def evaluate(self, bars: list[CandleBar], params: Any) -> BacktestResult:
        model = self.model()
        return BacktestEvaluator().evaluate(
            bars,
            model.evaluate(bars, params),
            fee_per_side=params.fee_per_side,
            slippage=params.slippage,
        )


@dataclass(frozen=True)
class VersionRun:
    version: str
    adapter: VersionAdapter
    config: Any
    selected_params: Any
    candidates: list[CandidateResult]
    validation_result: BacktestResult
    test_result: BacktestResult
    full_result: BacktestResult

    def summary_rows(self) -> list[dict[str, Any]]:
        return [
            self._summary_row("validation", self.validation_result.summary),
            self._summary_row("test", self.test_result.summary),
            self._summary_row("full", self.full_result.summary),
        ]

    def contract(self) -> dict[str, Any]:
        params = to_plain_dict(self.selected_params)
        params_camel = {snake_to_camel(key): value for key, value in params.items()}
        java_params = {key: value for key, value in params_camel.items() if key in self.adapter.java_param_fields}
        return {
            "version": self.version,
            "ioContractSchemaVersion": CONTRACT_SCHEMA_VERSION,
            "javaInteropReady": True,
            "modelClass": type(self.adapter.model()).__name__,
            "pythonParameterClass": type(self.selected_params).__name__,
            "javaParamsCamelCase": java_params,
            "selectedParams": params,
            "selectedParamsCamelCase": params_camel,
            "candidateCountReturned": len(self.candidates),
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
    adapters = [_adapter_from_module(path.stem) for path in MODEL_DIR.glob("v*.py") if VERSION_FILE.match(path.name)]
    adapters.sort(key=lambda adapter: adapter.number)
    return adapters


def to_plain_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return {
            field.name: _to_json_value(getattr(value, field.name))
            for field in dataclasses.fields(value)
            if field.metadata.get("serialize", True)
        }
    if isinstance(value, dict):
        return {str(key): _to_json_value(item) for key, item in value.items()}
    return {"value": _to_json_value(value)}


def summary_to_dict(summary: Any) -> dict[str, Any]:
    return to_plain_dict(summary)


def snake_to_camel(value: str) -> str:
    parts = value.split("_")
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def _adapter_from_module(module_name: str) -> VersionAdapter:
    module = importlib.import_module(f"{MODEL_PACKAGE}.{module_name}")
    version_suffix = module_name[1:]
    return VersionAdapter(
        name=module_name,
        module=module,
        config_class=getattr(module, f"BacktestConfigV{version_suffix}"),
        model_class=getattr(module, f"StrategyModelV{version_suffix}"),
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
