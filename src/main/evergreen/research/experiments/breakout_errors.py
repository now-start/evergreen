"""Read-only error attribution of frozen breakout forecasts and simulated trades."""

import argparse
import hashlib
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from evergreen.market import write_json
from evergreen.research.experiments.breakout_meta import MODELS
from evergreen.research.experiments.regime_stability import SEEDS


def event_metrics(rows: list[dict[str, Any]], *, strict: bool) -> dict[str, Any]:
    counts = dict.fromkeys(("tp", "fn", "fp", "tn"), 0)
    accepted: list[Decimal] = []
    rejected: list[Decimal] = []
    errors, prior_errors = [], []
    for row in rows:
        p, cutoff, prior, value = (
            Decimal(row[k]) for k in ("score", "threshold", "prior", "net_return")
        )
        if not all(v.is_finite() for v in (p, cutoff, prior, value)) or not all(
            0 <= v <= 1 for v in (p, cutoff, prior)
        ):
            raise ValueError("잘못된 확률·손익입니다")
        allow = p > cutoff if strict else p >= cutoff
        positive = value > 0
        counts[("tp" if positive else "fp") if allow else ("fn" if positive else "tn")] += 1
        (accepted if allow else rejected).append(value)
        errors.append((p - int(positive)) ** 2)
        prior_errors.append((prior - int(positive)) ** 2)

    def mean(values: list[Decimal]) -> str | None:
        return str(sum(values, Decimal(0)) / len(values)) if values else None

    return {
        "events": len(rows),
        **counts,
        "accepted_mean_return": mean(accepted),
        "rejected_mean_return": mean(rejected),
        "brier": mean(errors),
        "prior_brier": mean(prior_errors),
    }


def trade_pnl(result: dict[str, Any]) -> dict[str, Decimal]:
    """Pair all-in fills, including boundary settlement; reconcile against cash equity."""
    fills = result["fills"]
    if len(fills) % 2 or Decimal(result["btc"]) != 0:
        raise ValueError("미완결 포지션은 손익 분해할 수 없습니다")
    rows = {}
    for entry, exit_fill in zip(fills[::2], fills[1::2], strict=True):
        if entry["side"] != "buy" or exit_fill["side"] != "sell":
            raise ValueError("체결 순서 오류")
        if Decimal(entry["quantity"]) != Decimal(exit_fill["quantity"]):
            raise ValueError("매수·매도 수량이 다릅니다")
        signal = datetime.fromisoformat(entry["signal_time"]).isoformat()
        if signal in rows:
            raise ValueError("중복 진입 신호입니다")
        rows[signal] = (
            Decimal(exit_fill["price"]) * Decimal(exit_fill["quantity"])
            - Decimal(exit_fill["fee"])
            - Decimal(entry["price"]) * Decimal(entry["quantity"])
            - Decimal(entry["fee"])
        )
    expected = Decimal(result["final_equity"]) - Decimal(result["initial_capital"])
    if abs(sum(rows.values(), Decimal(0)) - expected) > Decimal(".000001"):
        raise ValueError("체결 손익이 최종 자산과 일치하지 않습니다")
    return rows


def analyze(
    source: Path,
    output: Path,
    *,
    seeds: tuple[int, ...] | None = None,
    models: tuple[str, ...] | None = None,
) -> None:
    output.mkdir(parents=True, exist_ok=False)
    hashes = {}

    def read(path: Path) -> Any:
        raw = path.read_bytes()
        hashes[str(path.relative_to(source))] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    protocol = read(source / "protocol.json")
    if read(source / "status.json")["status"] != "completed_partial_coverage":
        raise ValueError("완료된 연구 결과만 분석합니다")
    if protocol["experiment"] not in ("16", "17", "19", "20", "21", "22", "23"):
        raise ValueError("지원하지 않는 거래 수익성 실험입니다")
    if models is None:
        models = (
            tuple(protocol["models"])
            if protocol["experiment"] == "23"
            else ("linear-v1",)
            if protocol["experiment"] == "20"
            else MODELS
        )
    if seeds is None:
        seeds = (protocol["artifact_group_slot"],) if protocol["experiment"] == "23" else SEEDS
    mode = protocol.get("threshold_mode", "fixed")
    if mode not in ("fixed", "payoff", "weighted-score") or (
        mode != "payoff" and Decimal(protocol["entry_threshold"]) != Decimal(".50")
    ):
        raise ValueError("알 수 없는 진입 기준입니다")
    strict = mode != "fixed"
    events = read(source / "events.json")
    results = []
    for seed in seeds:
        directory = source / f"seed-{seed}"
        audits = read(directory / "audits.json")
        if not audits or any(a["status"] != "trained" for a in audits):
            raise ValueError("학습 분기가 누락됐습니다")
        for model in models:
            forecasts = {}
            # The later quarter owns a shared boundary, exactly as joined_evaluations does.
            for audit in audits:
                fold = directory / "folds" / audit["start"][:10]
                paths = sorted(fold.glob(f"*.{model}.json"))
                if not paths:
                    raise ValueError("분기 예측이 누락됐습니다")
                for path in paths:
                    for signal, score in read(path).items():
                        forecasts[signal] = {
                            "score": score,
                            "threshold": audit["entry_threshold"] if strict else ".50",
                            "prior": audit["weights"]["weighted_prior"]
                            if mode == "weighted-score"
                            else str(
                                Decimal(audit["training"]["positives"])
                                / audit["training"]["samples"]
                            ),
                        }
            blocks = sorted((directory / "continuous").iterdir())
            base_results = [read(b / "base.breakout-v1.json") for b in blocks]
            if not blocks:
                raise ValueError("연속 평가 블록이 없습니다")
            expected = {
                datetime.fromisoformat(t).isoformat()
                for result in base_results
                for t in [
                    result["start"],
                    *[p["time"] for p in result["equity_curve"] if p["phase"] == "close"],
                ]
            }
            if set(forecasts) != expected:
                raise ValueError("예측 시간 범위가 대조군과 다릅니다")
            # The final close cannot place a new order inside its evaluation block.
            available = expected - {
                datetime.fromisoformat(r["end"]).isoformat() for r in base_results
            }
            selected = [
                {**event, **forecasts[event["signal_time"]]}
                for event in events
                if event["status"] == "completed" and event["signal_time"] in available
            ]
            # Censored outcomes never become negatives. Quarterly-crossing outcomes are
            # allowed here only retrospectively, not for fitting or threshold selection.
            censored = sum(
                e["status"] != "completed" and e["signal_time"] in available for e in events
            )
            attribution = []
            for block, base_result in zip(blocks, base_results, strict=True):
                fitted_result = read(block / f"base.{model}.json")
                if any(
                    base_result[k] != fitted_result[k]
                    for k in ("start", "end", "costs", "initial_capital")
                ):
                    raise ValueError("손익 분해의 대조군 조건이 다릅니다")
                base, fitted = trade_pnl(base_result), trade_pnl(fitted_result)
                shared, removed, added = (
                    sorted(base.keys() & fitted.keys()),
                    sorted(base.keys() - fitted.keys()),
                    sorted(fitted.keys() - base.keys()),
                )
                shared_delta = sum((fitted[t] - base[t] for t in shared), Decimal(0))
                removed_delta = -sum((base[t] for t in removed), Decimal(0))
                added_delta = sum((fitted[t] for t in added), Decimal(0))
                attribution.append(
                    {
                        "block": block.name,
                        "shared": len(shared),
                        "removed": len(removed),
                        "added": len(added),
                        "removed_winners": sum(base[t] > 0 for t in removed),
                        "removed_nonwinners": sum(base[t] <= 0 for t in removed),
                        "shared_delta_krw": str(shared_delta),
                        "removed_delta_krw": str(removed_delta),
                        "added_delta_krw": str(added_delta),
                        "total_delta_krw": str(shared_delta + removed_delta + added_delta),
                    }
                )
            results.append(
                {
                    "seed": seed,
                    "model": model,
                    "score_semantics": "weighted_class_score_not_win_probability"
                    if mode == "weighted-score"
                    else "win_probability_proxy",
                    "censored_events": censored,
                    "metrics": event_metrics(selected, strict=strict),
                    "years": {
                        y: event_metrics(
                            [e for e in selected if e["signal_time"].startswith(y)], strict=strict
                        )
                        for y in sorted({e["signal_time"][:4] for e in selected})
                    },
                    "blocks": attribution,
                }
            )
    write_json(output / "results.json", results)
    write_json(
        output / "inputs.json",
        {
            "source": str(source),
            "sha256": hashes,
            "analysis_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )
    write_json(output / "status.json", {"status": "completed", "live_enabled": False})


def main() -> None:
    parser = argparse.ArgumentParser(description="돌파 모델 오류·실제 모의 체결 손익 분해")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    analyze(args.source, args.output)


if __name__ == "__main__":
    main()
