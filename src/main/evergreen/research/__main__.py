"""Run with `python -m evergreen.research`; isolated from server bootstrap."""

import argparse
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import httpx

from evergreen.market import collect, utc_hour
from evergreen.research.backtest import Costs
from evergreen.research.experiments.rules import run_study
from evergreen.research.report import compare


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="BTC/원화 오프라인 연구 · 계좌 접근과 실거래 없음")
    commands = parser.add_subparsers(dest="command", required=True)
    fetch = commands.add_parser("fetch", help="공개 API에서 확정된 시간봉 수집")
    fetch.add_argument("--start", required=True)
    fetch.add_argument("--end", required=True, help="종료 제외 · 시간대가 있는 UTC 정각")
    fetch.add_argument("--output", type=Path, required=True, help="새 데이터 저장 디렉터리")
    backtest = commands.add_parser("backtest", help="현금·단순 보유·기준 전략과 비용 스트레스 비교")
    backtest.add_argument("--dataset", type=Path, required=True)
    backtest.add_argument("--start", required=True, help="워밍업 이후 평가 시작 · UTC 정각")
    backtest.add_argument(
        "--capital", required=True, help="모의 원금(원) · 실제 계좌에 접근하지 않음"
    )
    for field in Costs.model_fields:
        backtest.add_argument("--" + field.replace("_", "-"), required=True)
    backtest.add_argument("--output", type=Path, required=True, help="새 실험 결과 디렉터리")
    study = commands.add_parser("study", help="사전 지정 실험: 개발 선택 후 별도 구간 검증")
    study.add_argument(
        "--experiment", choices=("01", "02r"), default="01", help="01: 추세·돌파, 02r: 평균회귀·RSI"
    )
    for name in ("development", "validation-a", "validation-b"):
        study.add_argument("--" + name, type=Path, required=True, help="품질 검사를 통과한 데이터")
    study.add_argument("--output", type=Path, required=True, help="새 실험 결과 디렉터리")
    deep = commands.add_parser("deep-study", help="실험 03: MLP·CNN 학습 및 규칙 전략 비교")
    deep.add_argument(
        "--training", type=Path, nargs=3, required=True, help="2024년 학습 데이터 3개"
    )
    deep.add_argument(
        "--selection", type=Path, nargs=3, required=True, help="2025년 모델 선택 데이터 3개"
    )
    deep.add_argument(
        "--tests", type=Path, nargs=2, required=True, help="2026년 최종 평가 A·B 데이터"
    )
    deep.add_argument("--output", type=Path, required=True, help="새 모델·실험 결과 디렉터리")
    early = commands.add_parser("early-study", help="실험 04: 조기 종료와 20 epoch 고정 비교")
    early.add_argument(
        "--training", type=Path, nargs=3, required=True, help="2024년 원본 데이터 3개"
    )
    early.add_argument(
        "--evaluation", type=Path, nargs=4, required=True, help="이미 관찰한 재비교 데이터 4개"
    )
    early.add_argument("--output", type=Path, required=True, help="새 비교 결과 디렉터리")
    hybrid = commands.add_parser("hybrid-study", help="실험 05: 고정 딥러닝과 규칙의 혼합 비교")
    hybrid.add_argument(
        "--trained-experiment", type=Path, required=True, help="완료된 실험 04 출력 경로"
    )
    hybrid.add_argument("--selection", type=Path, nargs=3, required=True)
    hybrid.add_argument(
        "--tests", type=Path, nargs=2, required=True, help="2026년 기존·새 평가 데이터"
    )
    hybrid.add_argument("--output", type=Path, required=True)
    meta = commands.add_parser("meta-study", help="실험 06: 규칙 진입 후보의 수익성 학습")
    meta.add_argument("--training", type=Path, nargs=3, required=True)
    meta.add_argument("--selection", type=Path, nargs=3, required=True)
    meta.add_argument("--test", type=Path, required=True)
    meta.add_argument(
        "--trained-experiment", type=Path, required=True, help="기존 비교 모델이 있는 실험 04"
    )
    meta.add_argument("--output", type=Path, required=True)
    breakout = commands.add_parser("breakout-study", help="실험 07: 고정 돌파 전략의 8월 검증")
    breakout.add_argument("--dataset", type=Path, required=True)
    breakout.add_argument("--trained-experiment", type=Path, required=True)
    breakout.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "breakout-study":
            from evergreen.research.experiments.breakout import run_breakout_study

            checks = run_breakout_study(args.dataset, args.trained_experiment, args.output)
            verdict = "연구 조건 통과" if all(checks.values()) else "연구 조건 미달"
            print(f"돌파 전략 {verdict}(실거래 승격 아님): {args.output / 'summary.md'}")
            return 0
        if args.command == "meta-study":
            from evergreen.research.experiments.meta import run_meta_study

            run_meta_study(
                args.training, args.selection, args.test, args.trained_experiment, args.output
            )
            print(f"메타 라벨링 재비교 완료(수익성 검증 아님): {args.output / 'summary.md'}")
            return 0
        if args.command == "hybrid-study":
            from evergreen.research.experiments.hybrid import run_hybrid_study

            run_hybrid_study(args.trained_experiment, args.selection, args.tests, args.output)
            print(f"혼합 전략 비교 완료(실거래 승격 아님): {args.output / 'summary.md'}")
            return 0
        if args.command == "early-study":
            from evergreen.research.experiments.early_stopping import run_early_study

            run_early_study(args.training, args.evaluation, args.output)
            print(f"조기 종료 재비교 완료(수익성 최종 검증 아님): {args.output / 'summary.md'}")
            return 0
        if args.command == "deep-study":
            from evergreen.research.experiments.deep import run_deep_study

            checks = run_deep_study(args.training, args.selection, args.tests, args.output)
            verdict = "연구 조건 통과" if all(checks.values()) else "수익성 미검증"
            print(f"딥러닝 {verdict}: {args.output / 'summary.md'}")
            return 0
        if args.command == "study":
            checks = run_study(
                {
                    name: getattr(args, name.replace("-", "_"))
                    for name in ("development", "validation-a", "validation-b")
                },
                args.output,
                experiment=args.experiment,
            )
            verdict = "연구 조건 통과" if all(checks.values()) else "수익성 미검증 — 연구 조건 미달"
            print(f"{verdict}: {args.output / 'summary.md'}")
            return 0
        start = utc_hour(datetime.fromisoformat(args.start))
        if args.command == "fetch":
            end = utc_hour(datetime.fromisoformat(args.end))
            with httpx.Client(follow_redirects=False) as client:
                candles = collect(client, start, end, args.output)
            print(f"품질 검사를 통과한 캔들 {len(candles)}개 저장: {args.output}")
        else:
            costs = Costs.model_validate(
                {field: getattr(args, field) for field in Costs.model_fields}
            )
            amount = Decimal(args.capital)
            compare(args.dataset, start, amount, costs, args.output)
            print(f"오프라인 비교 결과 12개 저장: {args.output / 'summary.md'}")
    except (ValueError, OSError, httpx.HTTPError, ArithmeticError, RuntimeError) as error:
        print(f"연구 실행 실패: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
