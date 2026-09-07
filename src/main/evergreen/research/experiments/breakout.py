"""Prespecified August evaluation of the user-selected breakout candidate."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from evergreen.market import load_dataset, write_json
from evergreen.research.backtest import Costs, Result
from evergreen.research.experiments.deep import _date, _prediction_artifacts
from evergreen.research.experiments.hybrid import frozen_models
from evergreen.research.experiments.rules import _four
from evergreen.research.report import STRATEGY_LABELS, compare, source_identity
from evergreen.strategies import STRATEGY_PARAMETERS, Strategy

INTERVAL = ("2026-07-24", "2026-08-01", "2026-09-01")
CANDIDATE: Strategy = "breakout-v1"
CONTROLS: tuple[Strategy, ...] = ("cash", "buy-hold", "sma-slow-v1", "mlp-v1")


def assess_breakout(results: list[Result]) -> dict[str, bool]:
    group = _four(results, CANDIDATE)
    return {
        "positive": all(row.net_return > 0 for row in group),
        "risk": all(not row.halted and row.max_drawdown <= Decimal(".10") for row in group),
        "exit_count": group[0].natural_exits >= 10,
    }


def run_breakout_study(dataset: Path, experiment: Path, output: Path) -> dict[str, bool]:
    output.mkdir(parents=True, exist_ok=False)
    costs = Costs.model_validate(
        dict(
            buy_fee=".0005",
            sell_fee=".0005",
            buy_slippage=".001",
            sell_slippage=".001",
            min_notional="5000",
            quantity_step=".00000001",
        )
    )
    write_json(
        output / "protocol.json",
        {
            "experiment": "07",
            "selected": CANDIDATE,
            "selection_method": "user_before_evaluation",
            "interval": INTERVAL,
            "controls": CONTROLS,
            "strategy_parameters": STRATEGY_PARAMETERS[CANDIDATE],
            "capital": "1000000",
            "costs": costs.model_dump(mode="json"),
            "checks": {
                "all_net_returns_positive": True,
                "max_drawdown": ".10",
                "halted": False,
                "min_base_exits": 10,
            },
            "live_enabled": False,
            "created_at": datetime.now(UTC).isoformat(),
            **source_identity(),
        },
    )
    try:
        candles, digest = load_dataset(dataset)
        if candles[0].open_time != _date(INTERVAL[0]) or candles[-1].close_time != _date(
            INTERVAL[2]
        ):
            raise ValueError("실험 07의 사전 지정 데이터 기간과 다릅니다")
        models = frozen_models(experiment, output / "baseline")
        predictions = _prediction_artifacts(
            {"mlp-v1": models["mlp-v1"]}, candles, _date(INTERVAL[1]), output / "predictions"
        )
        results = compare(
            dataset,
            _date(INTERVAL[1]),
            Decimal(1000000),
            costs,
            output / "august",
            strategies=(CANDIDATE, *CONTROLS),
            predictions=predictions,
        )
        checks = assess_breakout(results)
        rows = [
            "# 실험 07 — 고정 돌파 전략 추가 검증",
            "",
            "2026-08-01~09-01 UTC(종료 제외). 원금 100만 원, 편도 수수료 0.05%·슬리피지 0.1%.",
            "전략·기준은 평가 전에 고정했으며 결과에 따라 후보를 교체하거나 재학습하지 않았습니다.",
            "",
            "| 전략 | 기본 순수익률 | 최저 수익률(4조건) | 최악 낙폭 | 기본 청산 |",
            "|---|---:|---:|---:|---:|",
        ]
        for strategy in (CANDIDATE, *CONTROLS):
            group = _four(results, strategy)
            rows.append(
                f"| {STRATEGY_LABELS[strategy]} | {group[0].net_return:.2%} | "
                f"{min(r.net_return for r in group):.2%} | {max(r.max_drawdown for r in group):.2%} | "
                f"{group[0].natural_exits} |"
            )
        rows += ["", "## 사전 기준 판정", ""]
        labels = {
            "positive": "4조건 모두 양의 수익",
            "risk": "위험 중단 없음·낙폭 10% 이하",
            "exit_count": "기본 청산 10회 이상(종료 정산 제외)",
        }
        rows += [f"- {labels[key]}: {'통과' if value else '미달'}" for key, value in checks.items()]
        rows += [
            "",
            "청산 수는 최소 연구 점검이며 통계적 충분성을 보장하지 않습니다.",
            "한 달 결과로 장기 수익성을 입증하지 않습니다. Paper/Live 활성화나 계좌 접근은 없습니다.",
            "낙폭은 시가·종가 관측이며 10% 중단이 실제 손실 상한을 보장하지 않습니다.",
            "기존 실험 05의 7월 결측 실패는 유지되며 이번 8월 평가는 별도 실험입니다.",
            "",
        ]
        with (output / "summary.md").open("x", encoding="utf-8") as stream:
            stream.write("\n".join(rows))
        write_json(
            output / "status.json",
            {
                "status": "completed",
                "selected": CANDIDATE,
                "checks": checks,
                "research_checks_passed": all(checks.values()),
                "dataset_sha256": digest,
                "live_enabled": False,
            },
        )
        return checks
    except (ValueError, OSError, RuntimeError, ArithmeticError) as error:
        write_json(
            output / "failure.json",
            {"status": "failed", "error": str(error), "selected": CANDIDATE, "live_enabled": False},
        )
        if not (output / "summary.md").exists():
            with (output / "summary.md").open("x", encoding="utf-8") as stream:
                stream.write(f"# 실험 07 — 검증 실패\n\n{error}\n\n수익성 판정·실거래 승격 없음.\n")
        raise
