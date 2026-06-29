from __future__ import annotations

import argparse
from datetime import datetime, timezone

from evergreen_backtest.runner import BacktestRunRequest, run_backtest
from evergreen_backtest.walk_forward import WalkForwardConfig


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Evergreen versioned Python backtests.")
    parser.add_argument("--versions", default="all", help="Comma-separated versions, or 'all'. Example: v3,v4,v5")
    parser.add_argument("--market", default="KRW-BTC")
    parser.add_argument("--interval", default="auto", help="Candle interval: auto, days, minute_240, or 240m")
    parser.add_argument("--from-date", default="2020-01-01")
    parser.add_argument("--to-date")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--grid-parallelism", type=int, default=1)
    parser.add_argument("--disable-walk-forward", action="store_true")
    parser.add_argument("--train-window-days", type=int, default=1095)
    parser.add_argument("--test-window-days", type=int, default=180)
    parser.add_argument("--output-dir", default="outputs/backtests/latest")
    parser.add_argument("--no-write", action="store_true")
    args = parser.parse_args()

    versions = tuple(item.strip().lower() for item in args.versions.split(",") if item.strip())
    request = BacktestRunRequest(
        versions=versions,
        market=args.market,
        interval_key=args.interval,
        from_dt=_parse_date(args.from_date),
        to_dt=_parse_date(args.to_date) if args.to_date else None,
        common_config={"top_k": args.top_k, "grid_parallelism": args.grid_parallelism},
        walk_forward_config=WalkForwardConfig(
            enabled=not args.disable_walk_forward,
            train_window_days=args.train_window_days,
            test_window_days=args.test_window_days,
        ),
        output_dir=args.output_dir,
    )
    result = run_backtest(request)
    _print_summary(result.summary_rows())
    if not args.no_write:
        out_dir = result.write_outputs()
        print(f"\n결과 파일: {out_dir}")


def _parse_date(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _print_summary(rows: list[dict[str, object]]) -> None:
    headers = [
        "version",
        "phase",
        "selection_mode",
        "final_equity",
        "final_equity_bh",
        "cagr",
        "mdd",
        "trades",
    ]
    print(" | ".join(headers))
    print("-" * 86)
    for row in rows:
        print(" | ".join(_format(row.get(header)) for header in headers))


def _format(value: object) -> str:
    if isinstance(value, float):
        return f"{value:.6f}"
    return "" if value is None else str(value)


if __name__ == "__main__":
    main()
