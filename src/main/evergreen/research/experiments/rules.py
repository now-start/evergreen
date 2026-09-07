"""Prespecified experiments: development selection precedes validation evaluation."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from evergreen.market import load_dataset, write_json
from evergreen.research.backtest import Costs, Result
from evergreen.research.report import STRATEGY_LABELS, compare, source_identity
from evergreen.strategies import Strategy

CANDIDATES: tuple[Strategy, ...] = ("sma-slow-v1", "breakout-v1")
BASELINES: tuple[Strategy, ...] = ("cash", "buy-hold", "sma-trend-v0")
WINDOWS = {
    "development": ("2024-01-01", "2024-01-09", "2024-03-01"),
    "validation-a": ("2024-07-01", "2024-07-09", "2024-09-01"),
    "validation-b": ("2024-11-01", "2024-11-09", "2024-12-15"),
}
REVERSION_CANDIDATES: tuple[Strategy, ...] = ("band-reversion-v1", "rsi-rebound-v1")
REVERSION_WINDOWS = {
    "development": ("2025-01-03", "2025-01-11", "2025-03-01"),
    "validation-a": ("2025-04-01", "2025-04-09", "2025-06-01"),
    "validation-b": ("2025-07-01", "2025-07-09", "2025-09-01"),
}
WINDOW_LABELS = {"development": "개발", "validation-a": "검증 A", "validation-b": "검증 B"}
CHECK_LABELS = {
    "positive": "모든 구간·비용 조건에서 순수익률 양수",
    "risk": "모든 구간·비용 조건에서 위험 중단 없음 및 최대 낙폭 10% 이하",
    "beats_baseline": "모든 구간·비용 조건에서 기존 SMA 초과 수익",
    "exit_count": "두 검증 구간 기본 조건의 청산 횟수 합계 10회 이상",
}


def _four(results: list[Result], strategy: Strategy) -> list[Result]:
    selected = [row for row in results if row.strategy == strategy]
    if len(selected) != 4:
        raise ValueError(f"{strategy}: 비용 시나리오 4개가 필요합니다")
    # compare() returns results in base, slippage-x2, costs-x2, delay-1h order.
    return selected


def select_candidate(
    development: list[Result],
    *,
    candidates: tuple[Strategy, ...] = CANDIDATES,
) -> Strategy:
    scores = {
        strategy: (
            -min(row.net_return for row in _four(development, strategy)),
            max(row.max_drawdown for row in _four(development, strategy)),
            strategy,
        )
        for strategy in candidates
    }
    return min(candidates, key=scores.__getitem__)


def assess(
    windows: dict[str, list[Result]],
    selected: Strategy,
    *,
    candidate_ids: tuple[Strategy, ...] = CANDIDATES,
    comparisons: tuple[Strategy, ...] = ("sma-trend-v0",),
) -> dict[str, bool]:
    if set(windows) != set(WINDOWS) or selected not in candidate_ids or not comparisons:
        raise ValueError("고정된 개발·검증 A·검증 B 및 후보가 필요합니다")
    candidates = {name: _four(rows, selected) for name, rows in windows.items()}
    baselines = {
        strategy: {name: _four(rows, strategy) for name, rows in windows.items()}
        for strategy in comparisons
    }
    return {
        "positive": all(row.net_return > 0 for rows in candidates.values() for row in rows),
        "risk": all(
            not row.halted and row.max_drawdown <= Decimal("0.10")
            for rows in candidates.values()
            for row in rows
        ),
        "beats_baseline": all(
            row.net_return > baseline.net_return
            for baseline_windows in baselines.values()
            for name, rows in candidates.items()
            for row, baseline in zip(rows, baseline_windows[name], strict=True)
        ),
        "exit_count": sum(
            candidates[name][0].natural_exits for name in ("validation-a", "validation-b")
        )
        >= 10,
    }


def run_study(
    datasets: dict[str, Path], output: Path, *, experiment: str = "01"
) -> dict[str, bool]:
    if experiment not in ("01", "02r"):
        raise ValueError("지원하는 실험은 01 또는 02r입니다")
    windows = WINDOWS if experiment == "01" else REVERSION_WINDOWS
    candidates = CANDIDATES if experiment == "01" else REVERSION_CANDIDATES
    baselines = BASELINES if experiment == "01" else (*BASELINES, "sma-slow-v1")
    comparisons: tuple[Strategy, ...] = (
        ("sma-trend-v0",) if experiment == "01" else ("sma-trend-v0", "sma-slow-v1")
    )
    check_labels = CHECK_LABELS | {
        "beats_baseline": "모든 구간·비용 조건에서 "
        + " 및 ".join(STRATEGY_LABELS[s] for s in comparisons)
        + " 각각 초과 수익"
    }
    if set(datasets) != set(windows):
        raise ValueError("개발·검증 A·검증 B 데이터 경로가 필요합니다")
    output.mkdir(parents=True, exist_ok=False)
    costs = Costs(
        buy_fee=Decimal("0.0005"),
        sell_fee=Decimal("0.0005"),
        buy_slippage=Decimal("0.001"),
        sell_slippage=Decimal("0.001"),
        min_notional=Decimal("5000"),
        quantity_step=Decimal("0.00000001"),
    )
    capital = Decimal("1000000")
    completed: dict[str, list[Result]] = {}
    selected: Strategy | None = None
    try:
        write_json(
            output / "protocol.json",
            {
                "experiment": experiment,
                "mode": "offline",
                "windows": windows,
                "candidates": candidates,
                "comparison_strategies": comparisons,
                "costs": costs.model_dump(mode="json"),
                "capital": str(capital),
                "selection": "max worst return, min max drawdown, id",
                "criteria": check_labels,
                "created_at": datetime.now(UTC).isoformat(),
                **source_identity(),
            },
        )
        for name, (begin, start, end) in windows.items():
            candles, digest = load_dataset(datasets[name])
            first = datetime.fromisoformat(begin).replace(tzinfo=UTC)
            last = datetime.fromisoformat(end).replace(tzinfo=UTC)
            if candles[0].open_time != first or candles[-1].close_time != last:
                raise ValueError(f"{WINDOW_LABELS[name]}: 사전 지정한 데이터 기간과 다릅니다")
            strategies = baselines + candidates if selected is None else (*baselines, selected)
            completed[name] = compare(
                datasets[name],
                datetime.fromisoformat(start).replace(tzinfo=UTC),
                capital,
                costs,
                output / name,
                strategies=strategies,
            )
            if name == "development":
                selected = select_candidate(completed[name], candidates=candidates)
                write_json(
                    output / "selection.json",
                    {
                        "selected": selected,
                        "dataset_sha256": digest,
                        "selected_at": datetime.now(UTC).isoformat(),
                        "validation_evaluated": False,
                        "development_scores": {
                            strategy: {
                                "worst_return": str(
                                    min(r.net_return for r in _four(completed[name], strategy))
                                ),
                                "max_drawdown": str(
                                    max(r.max_drawdown for r in _four(completed[name], strategy))
                                ),
                            }
                            for strategy in candidates
                        },
                    },
                )
        if selected is None:
            raise ValueError("개발 후보 선택이 완료되지 않았습니다")
        checks = assess(completed, selected, candidate_ids=candidates, comparisons=comparisons)
        passed = all(checks.values())
        rows = [
            f"# 전략 개선 실험 {experiment} 결과",
            "",
            f"판정: **{'연구 조건 통과' if passed else '수익성 미검증 — 연구 조건 미달'}**",
            "",
            f"개발 구간에서 선정한 후보: {STRATEGY_LABELS[selected]}",
            "선정 기록 저장 후 별도 두 구간을 평가했으며 검증 결과로 후보를 교체하지 않았습니다.",
            "모의 원금은 구간별 100만 원입니다. 실제 계좌 접근·주문·모델 승격은 없습니다.",
            "",
            "| 구간 | 기본 순수익률 | 네 조건 중 최저 순수익률 | 네 조건 중 최대 낙폭 | 기본 청산 횟수 |",
            "|---|---:|---:|---:|---:|",
        ]
        for name, results in completed.items():
            candidate = _four(results, selected)
            rows.append(
                f"| {WINDOW_LABELS[name]} | {candidate[0].net_return:.2%} | "
                f"{min(r.net_return for r in candidate):.2%} | "
                f"{max(r.max_drawdown for r in candidate):.2%} | {candidate[0].natural_exits} |"
            )
        rows.extend(["", "## 사전 지정 통과 조건", ""])
        rows.extend(
            f"- {'통과' if ok else '미달'}: {check_labels[key]}" for key, ok in checks.items()
        )
        rows.extend(
            [
                "",
                "## 해석과 한계",
                "",
                "일부 기간 수익은 미래 수익의 증거가 아닙니다. 조건 미달 시 실거래 후보로 사용하지 않습니다.",
                "제한된 역사적 구간으로 연간 성과·장기 안정성을 주장할 수 없습니다.",
                (
                    "개발 기간의 1월 일부는 이미 관찰했습니다."
                    if experiment == "01"
                    else "최초 02 개발 데이터의 봉 누락으로 실패 후 02r에서 개발 기간만 사전 수정했습니다."
                ),
                "검증 기간도 이 실험 이후에는 미관측 구간이 아닙니다.",
                "구간별 원금·위험 상태는 독립적입니다. 수익률을 이어 붙여 복리 또는 연간 수익으로 보지 마세요.",
                "비용은 가정이며 호가 유동성·부분 체결은 반영하지 않았습니다. 10% 위험 중단은 손실 상한이 아닙니다.",
                "다음 실험은 실패 원인을 기록하고 새로운 검증 계획을 먼저 고정해야 합니다.",
                "",
                "## 상세 결과",
                "",
            ]
        )
        rows.extend(f"- [{WINDOW_LABELS[name]} 비교 결과]({name}/summary.md)" for name in windows)
        with (output / "summary.md").open("x", encoding="utf-8") as stream:
            stream.write("\n".join(rows) + "\n")
        write_json(
            output / "verdict.json",
            {
                "selected": selected,
                "checks": checks,
                "research_passed": passed,
                "live_enabled": False,
            },
        )
        return checks
    except (ValueError, OSError, ArithmeticError) as error:
        write_json(
            output / "failure.json",
            {
                "status": "failed",
                "error": str(error),
                "completed_windows": list(completed),
                "selected": selected,
            },
        )
        raise
