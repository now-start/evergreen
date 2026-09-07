"""Reproducible comparison artifacts, never overwriting an existing experiment."""

import hashlib
import json
import shutil
import subprocess
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from evergreen.market import load_dataset, write_json
from evergreen.research.backtest import Costs, Result, run_backtest
from evergreen.strategies import STRATEGY_PARAMETERS, Strategy, warmup_bars

STRATEGY_LABELS: dict[Strategy, str] = {
    "cash": "현금 유지",
    "buy-hold": "BTC 매수 후 보유",
    "sma-trend-v0": "기존 추세 SMA 20/60",
    "sma-slow-v1": "느린 추세 SMA 48/168",
    "breakout-v1": "168시간 고점 돌파",
    "band-reversion-v1": "24시간 가격 밴드 평균회귀",
    "rsi-rebound-v1": "추세 필터 RSI 반등",
    "mlp-v1": "딥러닝 MLP",
    "cnn-v1": "딥러닝 1D CNN",
    "mlp-trend-v1": "MLP + SMA 추세",
    "cnn-trend-v1": "CNN + SMA 추세",
    "mlp-breakout-v1": "MLP + 고점 돌파",
    "cnn-breakout-v1": "CNN + 고점 돌파",
    "mlp-rsi-v1": "MLP + RSI 반등",
    "cnn-rsi-v1": "CNN + RSI 반등",
    "trend-12h-v1": "SMA 추세·12h 제한",
    "breakout-12h-v1": "고점 돌파·12h 제한",
    "rsi-12h-v1": "RSI 반등·12h 제한",
    "meta-mlp-trend-v1": "메타 MLP·SMA 추세",
    "meta-cnn-trend-v1": "메타 CNN·SMA 추세",
    "meta-mlp-breakout-v1": "메타 MLP·고점 돌파",
    "meta-cnn-breakout-v1": "메타 CNN·고점 돌파",
    "meta-mlp-rsi-v1": "메타 MLP·RSI 반등",
    "meta-cnn-rsi-v1": "메타 CNN·RSI 반등",
}
SCENARIO_LABELS = {
    "base": "기본 비용",
    "slippage-x2": "슬리피지 2배",
    "costs-x2": "전체 비용 2배",
    "delay-1h": "체결 한 시간 지연",
}


def source_identity() -> dict[str, object]:
    source = Path(__file__).parent.parent
    hashes = {
        path.relative_to(source).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(source.rglob("*.py"))
    }
    identity: dict[str, object] = {"source_sha256": hashes, "commit": None, "worktree_dirty": None}
    git = shutil.which("git")
    if git:
        try:
            head = subprocess.run(  # noqa: S603 -- fixed git arguments, no shell or user command
                [git, "rev-parse", "HEAD"],
                cwd=source,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            status = subprocess.run(  # noqa: S603 -- fixed git arguments, no shell or user command
                [git, "status", "--porcelain"],
                cwd=source,
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            )
            identity.update(commit=head.stdout.strip(), worktree_dirty=bool(status.stdout))
        except (OSError, subprocess.SubprocessError):
            # Installed wheels need not have git metadata; source hashes remain authoritative.
            pass
    return identity


def compare(
    dataset: Path,
    start: datetime,
    capital: Decimal,
    costs: Costs,
    output: Path,
    *,
    strategies: tuple[Strategy, ...] = ("cash", "buy-hold", "sma-trend-v0"),
    predictions: dict[Strategy, dict[datetime, Decimal]] | None = None,
) -> list[Result]:
    if not strategies or len(set(strategies)) != len(strategies):
        raise ValueError("비교할 전략은 중복 없이 하나 이상 지정해야 합니다")
    candles, digest = load_dataset(dataset)
    scenarios: list[tuple[str, int, int, int]] = [
        ("base", 1, 1, 0),
        ("slippage-x2", 1, 2, 0),
        ("costs-x2", 2, 2, 0),
        ("delay-1h", 1, 1, 1),
    ]
    output.mkdir(parents=True, exist_ok=False)
    results: list[Result] = []
    filenames: list[str] = []
    rows = [
        "# 오프라인 전략 비교 결과",
        "",
        "연구용 결과입니다. 실거래·모델 승격을 실행하지 않으며 수익을 보장하지 않습니다.",
        "",
        f"평가 기간: {start.isoformat()} ~ {candles[-1].close_time.isoformat()} (종료 제외)",
        f"모의 원금: {capital:,.0f}원 · 데이터: `{digest}`",
        f"매수/매도 수수료: {costs.buy_fee:.4%} / {costs.sell_fee:.4%}",
        f"매수/매도 슬리피지: {costs.buy_slippage:.4%} / {costs.sell_slippage:.4%}",
        "비용과 유동성은 모의 가정이며 실제 과거 체결 조건을 검증한 값이 아닙니다.",
        "단순 보유에는 손실 중단이 없습니다. 거래 전략의 10% 중단도 손실 상한을 보장하지 않습니다.",
        "",
        "| 비용 조건 | 전략 | 순수익률 | 최대 낙폭 | 청산 횟수* | 위험 중단 | 주문 거부 |",
        "|---|---|---:|---:|---:|---|---:|",
    ]
    try:
        required = max(warmup_bars(strategy) for strategy in strategies)
        if sum(bar.open_time < start for bar in candles) < required:
            raise ValueError(f"공통 워밍업이 부족합니다: 최소 {required}개 봉 필요")
        for name, fee_multiple, slip_multiple, delay in scenarios:
            configured = Costs.model_validate(
                costs.model_dump()
                | {
                    "buy_fee": costs.buy_fee * fee_multiple,
                    "sell_fee": costs.sell_fee * fee_multiple,
                    "buy_slippage": costs.buy_slippage * slip_multiple,
                    "sell_slippage": costs.sell_slippage * slip_multiple,
                }
            )
            for strategy in strategies:
                result = run_backtest(
                    candles,
                    start,
                    capital,
                    configured,
                    strategy,
                    extra_delay_bars=delay,
                    predictions=(predictions or {}).get(strategy),
                )
                filename = f"{name}.{strategy}.json"
                write_json(output / filename, result.model_dump(mode="json"))
                results.append(result)
                filenames.append(filename)
                rows.append(
                    f"| {SCENARIO_LABELS[name]} | {STRATEGY_LABELS[strategy]} | {result.net_return:.2%} | "
                    f"{result.max_drawdown:.2%} | {result.natural_exits} | "
                    f"{'중단' if result.halted else '없음'} | {len(result.rejections)} |"
                )
        rows.extend(
            [
                "",
                "*청산 횟수는 종료 정산을 제외하고 위험 청산을 포함합니다.",
                "",
                "최종 평가금에는 매도 불가능한 BTC 잔량을 마지막 종가로 평가한 금액이 포함됩니다.",
                "잔량 평가액은 즉시 현금화 가능한 원화가 아닙니다. 상세 JSON의 원화·BTC 잔량을 확인하세요.",
                "JSON 필드명과 전략 ID는 재현·프로그램 호환성을 위해 영문 식별자를 유지합니다.",
            ]
        )
        with (output / "summary.md").open("x", encoding="utf-8") as stream:
            stream.write("\n".join(rows) + "\n")
        write_json(
            output / "manifest.json",
            {
                "status": "completed",
                "mode": "offline",
                "created_at": datetime.now(UTC).isoformat(),
                "dataset_sha256": digest,
                "dataset": str(dataset.resolve()),
                "strategy_parameters": {
                    strategy: STRATEGY_PARAMETERS[strategy] for strategy in strategies
                },
                "drawdown_limit": "0.10",
                "prediction_sha256": {
                    strategy: hashlib.sha256(
                        json.dumps(
                            {time.isoformat(): str(value) for time, value in values.items()},
                            sort_keys=True,
                        ).encode()
                    ).hexdigest()
                    for strategy, values in (predictions or {}).items()
                },
                "results": filenames,
                **source_identity(),
            },
        )
    except (ValueError, OSError) as error:
        write_json(
            output / "failure.json",
            {"status": "failed", "error": str(error), "partial_results": filenames},
        )
        raise
    return results
