"""trend2 — 컨퍼런스 우승작 'agent_05' 규칙을 스팟으로 포팅 + 오프라인 학습 MLP 게이트.

    trend2 = ema_gap_12 + 0.7*ema_gap_26 + ma_gap_10 + ma_gap_20
             + macd + macd_signal + 0.5*(return_lag_5 + return_lag_10)

결정 (스팟):
    * trend2 > 0이고 effective_score >= buy_cutoff이면 BUY
    * trend2 < 0이고 effective_score >= sell_cutoff이면 SELL (보유 중일 때만)

여기서 ``effective_score = rule_score * dl_score_modifier``이고 ``rule_score = |trend2| / scale``.
``scale``은 시리즈 첫 ``train_fraction`` 구간에 맞춘(fit) |trend2|의 70번째 백분위수다(미리보기
없음). 고정 ``rule_scale``을 넘기면 warmup부터 바로 거래한다.

**MLP 게이트 (프레임워크 네이티브 확장).** 프레임워크는 이미 "실행당 한 번, train 구간에서
fit" 하는 확장점을 갖는다 — ``features()``가 그것이고, ``walk_forward``가 그 fit 경계를 test
캔들이 안 보이게 정렬한다. v6는 MLP 게이트를 항상 적용한다: ``features()``가 같은 train 구간에서 MLP(14→32→12→1)를
학습해 바별 score_modifier를 만들고 ``effective_score``에 곱한다. 코어 변경 없이 전략 안에서 끝난다.

학습(PyTorch autograd)은 Python(오프라인)에 남긴다 — Java엔 torch가 없어 forward pass만 손으로 옮긴다.
Java ``V6StrategyEngine``은 :func:`export_v6`로 내보낸 가중치(``strategy-models/v6.json``)를
로드해 forward pass만 수행한다. DL 피처 순서·NaN 의미·forward pass는 Java와 정확히 일치해야 한다.
"""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


PREFERRED_INTERVAL_KEY = "minute_240"

DL_FEATURE_NAMES = (
    "candle_return",
    "range_pct",
    "volume_change_1",
    "return_lag_1",
    "return_lag_2",
    "return_lag_3",
    "return_lag_5",
    "return_lag_10",
    "ema_component",
    "ma_component",
    "macd_component",
    "momentum_component",
    "trend2",
    "trend2_signed_score",
)


@dataclass(frozen=True)
class Trend2Components:
    """trend2와 네 성분(EMA/MA/MACD/모멘텀). 성분 합은 ``ind.trend2_series``와 동일."""

    ema_component: list[float]
    ma_component: list[float]
    macd_component: list[float]
    momentum_component: list[float]
    trend2: list[float]


@dataclass(frozen=True)
class DeepLearningProfile:
    """불변 MLP forward-pass 프로파일(14→32→12→1). Python에서 오프라인 학습해 Java로 내보낸다."""

    feature_names: tuple[str, ...]
    center: tuple[float, ...]
    scale: tuple[float, ...]
    w1: tuple[tuple[float, ...], ...]
    b1: tuple[float, ...]
    w2: tuple[tuple[float, ...], ...]
    b2: tuple[float, ...]
    w3: tuple[float, ...]
    b3: float


@register("v6")
class Trend2Strategy(Strategy):
    def __init__(
        self,
        buy_cutoff: float = 0.055,
        sell_cutoff: float = 0.25,
        rule_scale: float | None = None,
        train_fraction: float = 0.6,
        dl_profile: DeepLearningProfile | None = None,
        dl_epochs: int = 130,
        dl_min_train_rows: int = 1000,
        dl_progress: bool = False,
    ) -> None:
        if buy_cutoff < 0.0 or sell_cutoff < 0.0:
            raise ValueError("cutoffs must be >= 0")
        if rule_scale is not None and rule_scale <= 0.0:
            raise ValueError("rule_scale must be > 0 when provided")
        if not (0.0 < train_fraction <= 1.0):
            raise ValueError("train_fraction must be in (0, 1]")
        if dl_epochs <= 0 or dl_min_train_rows <= 0:
            raise ValueError("dl_epochs and dl_min_train_rows must be > 0")
        self.buy_cutoff = buy_cutoff
        self.sell_cutoff = sell_cutoff
        self.rule_scale = rule_scale
        self.train_fraction = train_fraction
        self.dl_profile = dl_profile
        self.dl_epochs = dl_epochs
        self.dl_min_train_rows = dl_min_train_rows
        self.dl_progress = dl_progress

    def warmup(self) -> int:
        return 35

    def features(self, candles: Sequence[Candle]) -> dict[str, list[float]]:
        prep = self._prepare(candles)
        trend2 = prep.trend.trend2
        score = [
            abs(v) / prep.scale if (i >= prep.trade_from and math.isfinite(v) and prep.scale > 0.0) else math.nan
            for i, v in enumerate(trend2)
        ]
        if prep.profile is None:
            effective = list(score)
            probabilities = [math.nan] * len(score)
            modifiers = [1.0] * len(score)
        else:
            dl = _dl_scores(prep.profile, list(candles), prep.close, prep.trend, prep.scale)
            probabilities = [p for p, _ in dl]
            modifiers = [m for _, m in dl]
            effective = [s * m if math.isfinite(s) else math.nan for s, m in zip(score, modifiers, strict=True)]
        return {"trend2": trend2, "score": score, "effective_score": effective,
                "dl_probability": probabilities, "dl_score_modifier": modifiers}

    def decide(self, ctx: BarContext) -> Action:
        trend2 = ctx.feature("trend2")
        effective = ctx.feature("effective_score")
        if not math.isfinite(trend2) or not math.isfinite(effective):
            return Action.HOLD
        if trend2 > 0.0 and effective >= self.buy_cutoff:
            return Action.BUY
        if trend2 < 0.0 and effective >= self.sell_cutoff and ctx.in_position:
            return Action.SELL
        return Action.HOLD

    def _prepare(self, candles: Sequence[Candle]) -> "_Prepared":
        """규칙 scale을 확정하고 train 구간에서 MLP를 학습(또는 주어진 프로파일 사용). v6는 DL을 항상 적용한다."""
        close = [candle.close for candle in candles]
        trend = trend2_components(close)
        if self.rule_scale is not None:
            scale = self.rule_scale
            trade_from = 0
        else:
            trade_from = max(1, int(len(trend.trend2) * self.train_fraction))
            scale = _resolve_rule_scale(math.nan, trend.trend2[:trade_from])
        profile = self.dl_profile
        if profile is None:
            if self.rule_scale is not None:
                # 고정 rule_scale은 warmup부터 매매(trade_from=0)하므로 self-fit이 전체 이력(테스트 구간
                # 포함)으로 학습돼 lookahead가 된다. OOS 백테스트는 auto rule_scale + train_fraction을,
                # 라이브/재현 평가는 학습된 dl_profile을 직접 넘겨야 한다.
                raise ValueError(
                    "고정 rule_scale + self-fit DL은 in-sample lookahead가 된다. "
                    "auto rule_scale(+train_fraction)로 OOS 학습하거나 dl_profile을 직접 넘겨라."
                )
            # auto rule_scale 경로: train_fraction 구간에서만 학습 (walk_forward가 test 캔들과 분리).
            profile = _fit_dl_profile(list(candles[:trade_from]), scale, self.dl_epochs, self.dl_min_train_rows,
                                      progress=self.dl_progress)
        return _Prepared(close=close, trend=trend, scale=scale, trade_from=trade_from, profile=profile)


@dataclass(frozen=True)
class _Prepared:
    close: list[float]
    trend: Trend2Components
    scale: float
    trade_from: int
    profile: DeepLearningProfile | None


def trend2_components(close: list[float]) -> Trend2Components:
    """trend2를 네 성분과 합계로 분해. primitive는 ``ind``(Java 엔진과 검증된 동일 정의)를 재사용."""
    ema12 = ind.ewm_adjust_false(close, 12)
    ema26 = ind.ewm_adjust_false(close, 26)
    ma10 = ind.moving_average(close, 10)
    ma20 = ind.moving_average(close, 20)
    macd_line, macd_signal = ind.macd(close)
    ret5 = ind.return_lag_series(close, 5)
    ret10 = ind.return_lag_series(close, 10)

    ema_c: list[float] = []
    ma_c: list[float] = []
    macd_c: list[float] = []
    mom_c: list[float] = []
    trend2: list[float] = []
    for i, price in enumerate(close):
        if price <= 0.0 or not math.isfinite(price):
            ema_c.append(math.nan)
            ma_c.append(math.nan)
            macd_c.append(math.nan)
            mom_c.append(math.nan)
            trend2.append(math.nan)
            continue
        ema_value = ind.gap(price, ema12[i]) + (0.7 * ind.gap(price, ema26[i]))
        ma_value = ind.gap(price, ma10[i]) + ind.gap(price, ma20[i])
        macd_value = ind.safe(macd_line[i]) + ind.safe(macd_signal[i])
        momentum_value = 0.5 * (ind.safe(ret5[i]) + ind.safe(ret10[i]))
        ema_c.append(ema_value)
        ma_c.append(ma_value)
        macd_c.append(macd_value)
        mom_c.append(momentum_value)
        trend2.append(ema_value + ma_value + macd_value + momentum_value)
    return Trend2Components(ema_c, ma_c, macd_c, mom_c, trend2)


def _resolve_rule_scale(raw_rule_scale: float, trend2: list[float]) -> float:
    if math.isfinite(raw_rule_scale) and raw_rule_scale > 0.0:
        return raw_rule_scale
    # auto: |trend2| 70분위수. indicators의 검증된 단일 구현에 위임한다(중복 제거).
    return ind.robust_scale_q70([abs(value) for value in trend2])


def _dl_feature_row(
    bars: list[Candle],
    close: list[float],
    trend: Trend2Components,
    scale: float,
    index: int,
) -> list[float]:
    """딥러닝 forward pass용 14개 피처 행. Java ``V6StrategyEngine.deepLearningFeatureRow``와 동일."""
    bar = bars[index]
    previous_volume = bars[index - 1].volume if index > 0 else math.nan
    trend2 = trend.trend2[index]
    return [
        ind.gap(bar.close, bar.open),
        ((bar.high - bar.low) / bar.close) if math.isfinite(bar.close) and bar.close > 0.0 else 0.0,
        ind.gap(bar.volume, previous_volume),
        _return_lag_at(close, index, 1),
        _return_lag_at(close, index, 2),
        _return_lag_at(close, index, 3),
        _return_lag_at(close, index, 5),
        _return_lag_at(close, index, 10),
        ind.safe(trend.ema_component[index]),
        ind.safe(trend.ma_component[index]),
        ind.safe(trend.macd_component[index]),
        ind.safe(trend.momentum_component[index]),
        ind.safe(trend2),
        trend2 / scale if math.isfinite(trend2) and scale > 0.0 else 0.0,
    ]


def _return_lag_at(close: list[float], index: int, lag: int) -> float:
    if index < lag:
        return math.nan
    previous = close[index - lag]
    current = close[index]
    if previous > 0.0 and math.isfinite(previous) and math.isfinite(current):
        return (current / previous) - 1.0
    return math.nan


def _fit_dl_profile(
    train_bars: list[Candle],
    scale: float,
    epochs: int,
    min_train_rows: int,
    progress: bool = False,
    label: str = "v6",
) -> DeepLearningProfile | None:
    """train 구간에서만 MLP를 학습(다음 바 방향 라벨, PyTorch). torch/numpy 없거나 행이 부족하면 None(규칙 전용 폴백)."""
    try:
        import numpy as np
    except ImportError:
        return None

    close = [bar.close for bar in train_bars]
    trend = trend2_components(close)
    rows: list[list[float]] = []
    targets: list[float] = []
    for i in range(len(train_bars) - 1):
        current = train_bars[i].close
        next_close = train_bars[i + 1].close
        if not (math.isfinite(current) and math.isfinite(next_close) and current > 0.0):
            continue
        rows.append(_dl_feature_row(train_bars, close, trend, scale, i))
        targets.append(1.0 if (next_close / current) - 1.0 > 0.0 else 0.0)
    if len(rows) < min_train_rows:
        return None

    x_raw = np.asarray(rows, dtype=np.float64)
    y = np.asarray(targets, dtype=np.float64).reshape(-1, 1)
    center = np.nanmedian(x_raw, axis=0)
    q75 = np.nanpercentile(x_raw, 75, axis=0)
    q25 = np.nanpercentile(x_raw, 25, axis=0)
    std = np.nanstd(x_raw, axis=0)
    scale_vec = q75 - q25
    scale_vec = np.where(np.isfinite(scale_vec) & (np.abs(scale_vec) >= 1e-8), scale_vec, std)
    scale_vec = np.where(np.isfinite(scale_vec) & (np.abs(scale_vec) >= 1e-8), scale_vec, 1.0)
    center = np.where(np.isfinite(center), center, 0.0)
    x = np.clip(np.nan_to_num((x_raw - center) / scale_vec, nan=0.0, posinf=8.0, neginf=-8.0), -8.0, 8.0)

    try:
        import torch
        from torch import nn
    except ImportError:
        return None  # torch(dev 의존성)가 없으면 DL 없이 규칙 전용으로 폴백한다.

    torch.manual_seed(5005)
    features_x = torch.tensor(x, dtype=torch.float64)
    labels_y = torch.tensor(y, dtype=torch.float64)
    input_dim = features_x.shape[1]
    pos_rate = float(labels_y.mean())
    pos_weight = torch.tensor([(1.0 - pos_rate) / max(pos_rate, 1e-3)], dtype=torch.float64)

    # numpy 손코딩 backprop을 대체하는 autograd MLP(14→32→12→1). 은닉1에만 dropout(0.03).
    model = nn.Sequential(
        nn.Linear(input_dim, 32), nn.ReLU(), nn.Dropout(0.03),
        nn.Linear(32, 12), nn.ReLU(),
        nn.Linear(12, 1),
    ).double()
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.005, weight_decay=0.01)
    model.train()
    for epoch in range(1, epochs + 1):
        optimizer.zero_grad()
        loss_fn(model(features_x), labels_y).backward()
        optimizer.step()
        if progress:
            _print_train_progress(epoch, epochs, label)
    model.eval()

    # torch Linear는 weight를 (out, in)으로 저장한다 → numpy/Java forward pass가 쓰는 (in, out)으로
    # 전치(transpose)해 export해야 한다. b3는 스칼라. w3는 (12,) 벡터.
    layer1, layer2, layer3 = model[0], model[3], model[5]
    with torch.no_grad():
        w1 = layer1.weight.t().tolist()
        b1 = layer1.bias.tolist()
        w2 = layer2.weight.t().tolist()
        b2 = layer2.bias.tolist()
        w3 = layer3.weight.reshape(-1).tolist()
        b3 = float(layer3.bias.reshape(-1)[0])
    return DeepLearningProfile(
        feature_names=DL_FEATURE_NAMES,
        center=tuple(float(v) for v in center),
        scale=tuple(float(v) for v in scale_vec),
        w1=tuple(tuple(float(i) for i in row) for row in w1),
        b1=tuple(float(v) for v in b1),
        w2=tuple(tuple(float(i) for i in row) for row in w2),
        b2=tuple(float(v) for v in b2),
        w3=tuple(float(v) for v in w3),
        b3=b3,
    )


def _print_train_progress(epoch: int, epochs: int, label: str) -> None:
    """의존성 없는 학습 진행 바: epoch마다 한 줄을 갱신한다. 단일 프로세스에서만 깔끔하며,
    병렬 워커에서는 stdout이 뒤섞이므로 기본적으로 끈다(dl_progress=False)."""
    width = 20
    filled = int(width * epoch / epochs)
    bar = ("█" * filled) + ("·" * (width - filled))
    end = "\n" if epoch >= epochs else ""
    sys.stdout.write(f"\r  MLP 학습[{label}] [{bar}] {epoch}/{epochs} epoch{end}")
    sys.stdout.flush()


def _dl_scores(
    profile: DeepLearningProfile,
    bars: list[Candle],
    close: list[float],
    trend: Trend2Components,
    scale: float,
) -> list[tuple[float, float]]:
    """바별 (확률, score_modifier). score_modifier = agreement * (1 + 0.05*confidence). Java ``V6DeepLearningProfile.score``와 동일."""
    import numpy as np

    rows = [_dl_feature_row(bars, close, trend, scale, i) for i in range(len(bars))]
    x_raw = np.asarray(rows, dtype=np.float64)
    center = np.asarray(profile.center, dtype=np.float64)
    scale_vec = np.asarray(profile.scale, dtype=np.float64)
    x = np.clip(np.nan_to_num((x_raw - center) / scale_vec, nan=0.0, posinf=8.0, neginf=-8.0), -8.0, 8.0)
    w1 = np.asarray(profile.w1, dtype=np.float64)
    b1 = np.asarray(profile.b1, dtype=np.float64)
    w2 = np.asarray(profile.w2, dtype=np.float64)
    b2 = np.asarray(profile.b2, dtype=np.float64)
    w3 = np.asarray(profile.w3, dtype=np.float64).reshape(-1, 1)
    b3 = np.asarray([profile.b3], dtype=np.float64)
    a1 = np.maximum(x @ w1 + b1, 0.0)
    a2 = np.maximum(a1 @ w2 + b2, 0.0)
    logits = a2 @ w3 + b3
    probabilities = (1.0 / (1.0 + np.exp(-np.clip(logits.reshape(-1), -50.0, 50.0)))).tolist()

    scores: list[tuple[float, float]] = []
    for i, probability in enumerate(probabilities):
        if not math.isfinite(probability):
            scores.append((probability, 1.0))
            continue
        model_side = 1 if probability >= 0.5 else -1
        rule_side = 1 if trend.trend2[i] > 0.0 else (-1 if trend.trend2[i] < 0.0 else 0)
        confidence = abs(probability - 0.5) * 2.0
        agreement = 1.0 if model_side == rule_side else 0.94
        scores.append((probability, agreement * (1.0 + (0.05 * confidence))))
    return scores


# --- Java용 모델 번들 export (v6.json) -----------------------------------------

def export_v6(
    out_path: str | Path,
    bars: list[Candle],
    *,
    dl_epochs: int = 130,
    dl_min_train_rows: int = 1000,
    provenance: dict[str, Any] | None = None,
    golden_path: str | Path | None = None,
    progress: bool = True,
) -> DeepLearningProfile | None:
    """전체 ``bars``로 규칙 scale(auto-q70)과 MLP를 학습해 프로덕션 ``v6.json``을 쓴다.

    Java ``V6StrategyEngine``이 로드하는 스키마(version/params/deepLearning)를 낸다. 선택적으로
    자바 패리티 골든 픽스처(candles + Python 기대 신호)도 함께 낼 수 있다.
    """
    strategy = Trend2Strategy(train_fraction=1.0, dl_epochs=dl_epochs, dl_min_train_rows=dl_min_train_rows,
                              dl_progress=progress)
    prep = strategy._prepare(bars)
    if not math.isfinite(prep.scale) or prep.scale <= 0.0:
        raise ValueError(f"resolved rule_scale is not finite/positive, refusing to export: {prep.scale}")
    if prep.profile is None:
        # 조용히 DL-disabled 번들을 쓰지 않는다: numpy 부재나 학습 행 부족은 배포 버그다.
        raise ValueError("v6 MLP 학습이 프로파일을 만들지 못했다(numpy 부재 또는 학습 행 부족); export를 중단한다.")
    prov = _augment_provenance(provenance, bars, prep)

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(
        json.dumps(_bundle_dict(strategy, prep, prov), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    # 표준화+MLP를 ONNX 그래프로도 내보낸다(Java onnxruntime 인퍼런스용). v6.json은 매니페스트/파라미터.
    _export_onnx(prep.profile, out_file.with_suffix(".onnx"))

    if golden_path is not None:
        # 추론과 동일 의미(고정 scale + 학습된 프로파일, warmup부터 매매)로 골든을 만든다.
        # export용 train_fraction=1.0 전략을 그대로 쓰면 trade_from==len이라 모든 score가 NaN이 된다.
        eval_strategy = Trend2Strategy(
            rule_scale=prep.scale, buy_cutoff=strategy.buy_cutoff, sell_cutoff=strategy.sell_cutoff,
            dl_profile=prep.profile,
        )
        golden_file = Path(golden_path)
        golden_file.parent.mkdir(parents=True, exist_ok=True)
        golden_file.write_text(
            json.dumps(_golden_dict(eval_strategy, prep, bars, prov), ensure_ascii=False, indent=2, allow_nan=False),
            encoding="utf-8",
        )
    return prep.profile


def _augment_provenance(provenance: dict[str, Any] | None, bars: list[Candle], prep: "_Prepared") -> dict[str, Any]:
    """재현성 검증을 위해 런타임(numpy/python)·데이터 해시·학습 seed·rule_scale을 provenance에 덧붙인다."""
    import hashlib
    import platform

    try:
        import numpy as np
        numpy_version: str | None = np.__version__
    except ImportError:
        numpy_version = None
    close_digest = hashlib.sha256(",".join(repr(bar.close) for bar in bars).encode("utf-8")).hexdigest()[:16]
    merged = dict(provenance or {})
    merged.setdefault("bars", len(bars))
    merged.update({
        "numpy": numpy_version,
        "python": platform.python_version(),
        "closeSha256_16": close_digest,
        "trainSeed": 5005,
        "ruleScale": prep.scale,
    })
    return merged


def _export_onnx(profile: DeepLearningProfile, out_path: str | Path) -> None:
    """표준화(center/scale)+MLP를 하나의 ONNX 그래프로 export한다.

    입력 ``features`` (N, D) = raw 14 피처 → 출력 ``probability`` (N,). 표준화·clip·relu·sigmoid가
    그래프에 baking돼 있어 Java onnxruntime는 raw 피처만 넣으면 확률을 얻는다. 모델 구조가 커져도
    이 export 경로/입출력 계약은 그대로라 Java 인퍼런스 코드를 바꿀 필요가 없다(확장성).
    """
    import torch
    from torch import nn

    input_dim = len(profile.center)

    class _StdMLP(nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.register_buffer("center", torch.tensor(profile.center, dtype=torch.float64))
            self.register_buffer("scale", torch.tensor(profile.scale, dtype=torch.float64))
            self.l1 = nn.Linear(input_dim, len(profile.b1)).double()
            self.l2 = nn.Linear(len(profile.b1), len(profile.b2)).double()
            self.l3 = nn.Linear(len(profile.b2), 1).double()
            with torch.no_grad():
                self.l1.weight.copy_(torch.tensor(profile.w1, dtype=torch.float64).t())
                self.l1.bias.copy_(torch.tensor(profile.b1, dtype=torch.float64))
                self.l2.weight.copy_(torch.tensor(profile.w2, dtype=torch.float64).t())
                self.l2.bias.copy_(torch.tensor(profile.b2, dtype=torch.float64))
                self.l3.weight.copy_(torch.tensor(profile.w3, dtype=torch.float64).reshape(1, -1))
                self.l3.bias.copy_(torch.tensor([profile.b3], dtype=torch.float64))

        def forward(self, x):  # noqa: ANN001
            z = torch.clamp(torch.nan_to_num((x - self.center) / self.scale, nan=0.0, posinf=8.0, neginf=-8.0), -8.0, 8.0)
            z = torch.relu(self.l1(z))
            z = torch.relu(self.l2(z))
            return torch.sigmoid(self.l3(z)).reshape(-1)

    out_file = Path(out_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        _StdMLP().eval(),
        torch.zeros((1, input_dim), dtype=torch.float64),
        str(out_file),
        input_names=["features"],
        output_names=["probability"],
        dynamic_axes={"features": {0: "batch"}, "probability": {0: "batch"}},
        opset_version=18,
    )
    # dynamo exporter가 가중치를 외부 파일(.data)로 분리할 수 있다 → 단일 자립 파일로 합쳐
    # Java 클래스패스 리소스 로딩을 단순화한다(사이드카 .data 제거).
    import onnx

    onnx.save_model(onnx.load(str(out_file)), str(out_file), save_as_external_data=False)
    sidecar = out_file.with_name(out_file.name + ".data")
    if sidecar.exists():
        sidecar.unlink()


def build_synthetic_bars(count: int, *, seed_price: float = 30_000_000.0) -> list[Candle]:
    """재현 가능한 4시간봉 시리즈(numpy RNG 미사용). 골든 픽스처 생성 전용."""
    if count <= 0:
        raise ValueError("count must be > 0")
    start = datetime(2020, 1, 1, tzinfo=timezone.utc)
    bars: list[Candle] = []
    price = seed_price
    state = 123_456_789
    for i in range(count):
        state = (1_103_515_245 * state + 12_345) & 0x7FFFFFFF
        noise = (state / 0x7FFFFFFF) - 0.5
        ret = (math.sin(i / 18.0) * 0.012) + (noise * 0.02)
        open_price = price
        close_price = max(1.0, price * (1.0 + ret))
        high = max(open_price, close_price) * (1.0 + (abs(noise) * 0.01))
        low = min(open_price, close_price) * (1.0 - (abs(noise) * 0.01))
        bars.append(Candle(timestamp=start + timedelta(hours=4 * i), open=open_price, high=high,
                           low=low, close=close_price, volume=100.0 + float(state % 1000)))
        price = close_price
    return bars


def _profile_to_dict(profile: DeepLearningProfile) -> dict[str, Any]:
    return {
        "enabled": True,
        "featureNames": list(profile.feature_names),
        "inputDim": len(profile.center),
        "hidden1": len(profile.b1),
        "hidden2": len(profile.b2),
        "center": [float(v) for v in profile.center],
        "scale": [float(v) for v in profile.scale],
        "w1": [[float(i) for i in row] for row in profile.w1],
        "b1": [float(v) for v in profile.b1],
        "w2": [[float(i) for i in row] for row in profile.w2],
        "b2": [float(v) for v in profile.b2],
        "w3": [float(v) for v in profile.w3],
        "b3": float(profile.b3),
    }


def _bundle_dict(strategy: Trend2Strategy, prep: _Prepared, provenance: dict[str, Any] | None) -> dict[str, Any]:
    deep_learning = _profile_to_dict(prep.profile) if prep.profile is not None else {"enabled": False}
    bundle: dict[str, Any] = {
        "version": "v6",
        "intervalKey": PREFERRED_INTERVAL_KEY,
        "onnxModel": "v6.onnx",
        "params": {
            "ruleScale": float(prep.scale),
            "buyCutoff": float(strategy.buy_cutoff),
            "sellCutoff": float(strategy.sell_cutoff),
        },
        "deepLearning": deep_learning,
    }
    if provenance is not None:
        bundle["provenance"] = provenance
    return bundle


def _json_number(value: float) -> float | None:
    return float(value) if math.isfinite(value) else None


def _golden_dict(strategy: Trend2Strategy, prep: _Prepared, bars: list[Candle], provenance: dict[str, Any] | None) -> dict[str, Any]:
    feats = strategy.features(bars)
    trend2, score, effective = feats["trend2"], feats["score"], feats["effective_score"]
    probabilities, modifiers = feats["dl_probability"], feats["dl_score_modifier"]
    expected: list[dict[str, Any]] = []
    held = 0.0
    for i in range(len(bars)):
        eff, t2 = effective[i], trend2[i]
        setup_buy = math.isfinite(eff) and t2 > 0.0 and eff >= strategy.buy_cutoff
        setup_sell = math.isfinite(eff) and t2 < 0.0 and eff >= strategy.sell_cutoff and held > 0.0
        target = 0.0 if setup_sell else (1.0 if setup_buy else held)
        action = "BUY" if target > held + 1e-12 else ("SELL" if target < held - 1e-12 else "HOLD")
        expected.append({
            "index": i,
            "timestamp": bars[i].timestamp.isoformat(),
            "action": action,
            "signalReason": _signal_reason(action, setup_buy, setup_sell),
            "targetPositionRatio": target,
            "currentOpen": held,
            "ruleScore": _json_number(score[i]),
            "effectiveScore": _json_number(eff),
            "dlProbability": _json_number(probabilities[i]),
            "dlScoreModifier": _json_number(modifiers[i]),
            "trend2": _json_number(t2),
        })
        held = target
    bundle = _bundle_dict(strategy, prep, provenance)
    bundle["candles"] = [
        {"timestamp": b.timestamp.isoformat(), "open": b.open, "high": b.high, "low": b.low, "close": b.close, "volume": b.volume}
        for b in bars
    ]
    bundle["expectedSignals"] = expected
    return bundle


def _signal_reason(action: str, setup_buy: bool, setup_sell: bool) -> str:
    if action == "BUY":
        return "BUY_TREND2_POSITIVE"
    if action == "SELL":
        return "SELL_TREND2_NEGATIVE"
    if setup_buy:
        return "SETUP_BUY_TREND2"
    if setup_sell:
        return "SETUP_SELL_TREND2"
    return "NONE"
