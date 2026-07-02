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

학습(PyTorch autograd)은 Python(오프라인)에 남긴다. :func:`export_v6`가 표준화+MLP를 self-contained
ONNX 그래프(``strategy-models/v6.onnx``)로 내보내고, Java ``V6ModelConfig``가 이를 ONNX Runtime으로
로드해 인퍼런스한다(JSON 매니페스트 없음). DL 피처 순서·NaN 의미·표준화는 Java 피처 생성과 정확히 일치해야 한다.
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

# early stopping용 검증셋 최소 행 수. 이보다 작으면 val 손실 신호가 노이즈라 early stopping을 끈다.
_MIN_VAL_ROWS = 50

# v6 DL 하이퍼파라미터 HPO 기본 그리드. grid_search/walk_forward가 create("v6", **params)로 생성하므로
# 키는 Trend2Strategy 생성자 kwargs다. dl_epochs는 상한이고 early stopping이 실제 종료를 정한다.
# 백테스트 metric(calmar 등, OOS)으로 순위를 매겨 best를 고른다. (조합 수 x MLP 학습이라 비용이 든다.)
DEFAULT_DL_PARAM_GRID = {
    "dl_lr": [0.002, 0.005, 0.01],
    "dl_dropout": [0.0, 0.05, 0.15],
    "dl_hidden1": [16, 32, 64],
    "dl_epochs": [400],
}

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
        dl_hidden1: int = 32,
        dl_hidden2: int = 12,
        dl_lr: float = 0.005,
        dl_weight_decay: float = 0.01,
        dl_dropout: float = 0.03,
        dl_val_fraction: float = 0.15,
        dl_patience: int = 15,
    ) -> None:
        if buy_cutoff < 0.0 or sell_cutoff < 0.0:
            raise ValueError("cutoffs must be >= 0")
        if rule_scale is not None and rule_scale <= 0.0:
            raise ValueError("rule_scale must be > 0 when provided")
        if not (0.0 < train_fraction <= 1.0):
            raise ValueError("train_fraction must be in (0, 1]")
        if dl_epochs <= 0 or dl_min_train_rows <= 0:
            raise ValueError("dl_epochs and dl_min_train_rows must be > 0")
        if dl_hidden1 <= 0 or dl_hidden2 <= 0:
            raise ValueError("dl_hidden1 and dl_hidden2 must be > 0")
        if dl_lr <= 0.0:
            raise ValueError("dl_lr must be > 0")
        if dl_weight_decay < 0.0:
            raise ValueError("dl_weight_decay must be >= 0")
        if not (0.0 <= dl_dropout < 1.0):
            raise ValueError("dl_dropout must be in [0, 1)")
        if not (0.0 <= dl_val_fraction < 1.0):
            raise ValueError("dl_val_fraction must be in [0, 1)")
        if dl_patience < 0:
            raise ValueError("dl_patience must be >= 0")
        self.buy_cutoff = buy_cutoff
        self.sell_cutoff = sell_cutoff
        self.rule_scale = rule_scale
        self.train_fraction = train_fraction
        self.dl_profile = dl_profile
        self.dl_epochs = dl_epochs
        self.dl_min_train_rows = dl_min_train_rows
        self.dl_progress = dl_progress
        self.dl_hidden1 = dl_hidden1
        self.dl_hidden2 = dl_hidden2
        self.dl_lr = dl_lr
        self.dl_weight_decay = dl_weight_decay
        self.dl_dropout = dl_dropout
        self.dl_val_fraction = dl_val_fraction
        self.dl_patience = dl_patience

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
            profile = _fit_dl_profile(
                list(candles[:trade_from]), scale, self.dl_epochs, self.dl_min_train_rows,
                progress=self.dl_progress, hidden1=self.dl_hidden1, hidden2=self.dl_hidden2,
                lr=self.dl_lr, weight_decay=self.dl_weight_decay, dropout=self.dl_dropout,
                val_fraction=self.dl_val_fraction, patience=self.dl_patience)
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
    *,
    hidden1: int = 32,
    hidden2: int = 12,
    lr: float = 0.005,
    weight_decay: float = 0.01,
    dropout: float = 0.03,
    val_fraction: float = 0.15,
    patience: int = 15,
) -> DeepLearningProfile | None:
    """train 구간에서만 MLP를 학습(다음 바 방향 라벨, PyTorch). torch/numpy 없거나 행이 부족하면 None(규칙 전용 폴백).

    ``epochs``는 상한이며, train 구간 뒤쪽 ``val_fraction``을 검증셋으로 떼어 val BCE가 ``patience`` epoch
    동안 개선되지 않으면 조기 종료하고 best 가중치를 복원한다(과적합/과소적합 방지). 14개 피처 표준화 통계
    (center/scale)는 train 하위구간에서만 적합해 val 정보 누수를 막는다. HP(hidden/lr/weight_decay/dropout)는
    walk_forward HPO로 스윕할 수 있도록 인자로 노출한다. val이 너무 작으면 early stopping 없이 전체 epoch를 돈다(재현 가능).

    주의(부분 누수): ``scale``(rule_scale)은 바깥 train 구간 전체(=inner val 포함)에서 적합돼 ``trend2_signed_score``
    피처에 쓰이므로, early stopping의 val은 이 스칼라 하나에 대해선 완전한 holdout이 아니다. 추론(Java/ONNX)과
    동일한 rule_scale을 써야 하는 제약상 의도된 것이며(train/serve 정합), OOS 백테스트 구간에는 영향이 없다."""
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

    try:
        import copy

        import torch
        from torch import nn
    except ImportError:
        return None  # torch(dev 의존성)가 없으면 DL 없이 규칙 전용으로 폴백한다.

    x_raw_all = np.asarray(rows, dtype=np.float64)
    y_all = np.asarray(targets, dtype=np.float64).reshape(-1, 1)

    # 시간순 유지 val 분리(뒤쪽 val_fraction). val이 최소 _MIN_VAL_ROWS는 돼야 early stopping 신호가 의미 있고,
    # 남는 train이 min_train_rows 이상이어야 한다. 아니면 val 없이 전체 epoch 학습.
    n_total = x_raw_all.shape[0]
    n_val = int(n_total * val_fraction) if (patience > 0 and val_fraction > 0.0) else 0
    if n_val < _MIN_VAL_ROWS or (n_total - n_val) < min_train_rows:
        n_val = 0
    n_train = n_total - n_val

    # 표준화(center/scale)는 train 하위구간에서만 적합 → val 정보 누수 방지.
    x_train_raw = x_raw_all[:n_train]
    center = np.nanmedian(x_train_raw, axis=0)
    q75 = np.nanpercentile(x_train_raw, 75, axis=0)
    q25 = np.nanpercentile(x_train_raw, 25, axis=0)
    std = np.nanstd(x_train_raw, axis=0)
    scale_vec = q75 - q25
    scale_vec = np.where(np.isfinite(scale_vec) & (np.abs(scale_vec) >= 1e-8), scale_vec, std)
    scale_vec = np.where(np.isfinite(scale_vec) & (np.abs(scale_vec) >= 1e-8), scale_vec, 1.0)
    center = np.where(np.isfinite(center), center, 0.0)

    def _standardize(arr: "np.ndarray") -> "np.ndarray":
        return np.clip(np.nan_to_num((arr - center) / scale_vec, nan=0.0, posinf=8.0, neginf=-8.0), -8.0, 8.0)

    torch.manual_seed(5005)
    train_x = torch.tensor(_standardize(x_raw_all[:n_train]), dtype=torch.float64)
    train_y = torch.tensor(y_all[:n_train], dtype=torch.float64)
    has_val = n_val > 0
    if has_val:
        val_x = torch.tensor(_standardize(x_raw_all[n_train:]), dtype=torch.float64)
        val_y = torch.tensor(y_all[n_train:], dtype=torch.float64)
    input_dim = train_x.shape[1]
    pos_rate = float(train_y.mean())
    pos_weight = torch.tensor([(1.0 - pos_rate) / max(pos_rate, 1e-3)], dtype=torch.float64)

    # autograd MLP(input_dim→hidden1→hidden2→1). Dropout 레이어는 (0.0이어도) weight 추출 인덱스(0/3/5)
    # 안정성을 위해 항상 유지한다.
    model = nn.Sequential(
        nn.Linear(input_dim, hidden1), nn.ReLU(), nn.Dropout(dropout),
        nn.Linear(hidden1, hidden2), nn.ReLU(),
        nn.Linear(hidden2, 1),
    ).double()
    loss_fn = nn.BCEWithLogitsLoss(pos_weight=pos_weight)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=weight_decay)

    best_state = None
    best_val = math.inf
    since_best = 0
    epochs_iter = _epoch_progress(epochs, label) if progress else range(1, epochs + 1)
    for _epoch in epochs_iter:
        model.train()
        optimizer.zero_grad()
        loss_fn(model(train_x), train_y).backward()
        optimizer.step()
        if has_val:
            model.eval()
            with torch.no_grad():
                val_loss = float(loss_fn(model(val_x), val_y))
            if val_loss < best_val - 1e-6:
                best_val, since_best = val_loss, 0
                best_state = copy.deepcopy(model.state_dict())
            else:
                since_best += 1
                if since_best >= patience:
                    break
    if best_state is not None:
        model.load_state_dict(best_state)  # 조기 종료 시 best val 가중치 복원
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


def _epoch_progress(epochs: int, label: str):
    """학습 epoch 진행 바를 그리며 1..epochs를 yield한다. tqdm이 있으면 노트북(위젯/HTML)과
    터미널 모두에서 깔끔하게 렌더되고, 없으면 의존성 없는 ``\\r`` 폴백 바를 쓴다. 병렬 워커에서는
    progress=False라 호출되지 않는다(stdout이 뒤섞이므로)."""
    try:
        from tqdm.auto import tqdm as _tqdm
    except ImportError:
        for epoch in range(1, epochs + 1):
            _print_train_progress(epoch, epochs, label)
            yield epoch
        return
    yield from _tqdm(range(1, epochs + 1), desc=f"MLP 학습[{label}]", unit="epoch", leave=True)


def _print_train_progress(epoch: int, epochs: int, label: str) -> None:
    """tqdm이 없을 때 쓰는 의존성 없는 폴백 진행 바: epoch마다 한 줄을 갱신한다."""
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
    """바별 (확률, score_modifier). score_modifier = agreement * (1 + 0.05*confidence). Java ``V6DeepLearningScore.of``와 동일."""
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


# --- Java용 모델 export (v6.onnx) ----------------------------------------------

def export_v6(
    out_path: str | Path,
    bars: list[Candle],
    *,
    provenance: dict[str, Any] | None = None,
    golden_path: str | Path | None = None,
    progress: bool = True,
    **dl_params: Any,
) -> DeepLearningProfile | None:
    """전체 ``bars``로 규칙 scale(auto-q70)과 MLP를 학습해 프로덕션 ``v6.onnx``를 쓴다.

    Java ``V6ModelConfig``가 ONNX Runtime으로 로드하는 self-contained 그래프(표준화+MLP)를 낸다.
    JSON 매니페스트는 쓰지 않으며(규칙 파라미터는 config로 관리), 해결된 rule_scale을 출력·반환하므로
    운영자는 이를 config ``evergreen.trading.v6.rule-scale``에 맞춰야 한다. ``out_path``는 모델을 쓸
    경로이며 확장자는 ``.onnx``로 맞춰진다(예: ``src/main/resources/strategy-models/v6.onnx``).
    선택적으로 자바 패리티 골든 픽스처(candles + 기대 신호 JSON + 짝 onnx)도 함께 낼 수 있다.

    ``**dl_params``는 :class:`Trend2Strategy`의 DL 하이퍼파라미터(``dl_epochs``, ``dl_min_train_rows``,
    ``dl_hidden1/2``, ``dl_lr``, ``dl_weight_decay``, ``dl_dropout``, ``dl_val_fraction``, ``dl_patience``)로
    그대로 전달된다 — ``walk_forward``/``grid_search`` HPO로 찾은 best 파라미터를 여기 넘겨 그 설정으로
    배포 모델을 학습·export한다. 지정하지 않으면 전략 기본값(early stopping 포함)을 쓴다."""
    reserved = {"train_fraction", "dl_progress", "dl_profile"} & dl_params.keys()
    if reserved:
        # 이 값들은 export가 직접 제어한다(전체 이력 학습). HPO best params를 그대로 넘길 때 충돌을 명확히 막는다.
        raise ValueError(f"export_v6는 {sorted(reserved)}을(를) 직접 제어하므로 넘길 수 없다. DL 하이퍼파라미터만 넘겨라.")
    strategy = Trend2Strategy(train_fraction=1.0, dl_progress=progress, **dl_params)
    prep = strategy._prepare(bars)
    if not math.isfinite(prep.scale) or prep.scale <= 0.0:
        raise ValueError(f"resolved rule_scale is not finite/positive, refusing to export: {prep.scale}")
    if prep.profile is None:
        # 조용히 DL-disabled 번들을 쓰지 않는다: numpy 부재나 학습 행 부족은 배포 버그다.
        raise ValueError("v6 MLP 학습이 프로파일을 만들지 못했다(numpy 부재 또는 학습 행 부족); export를 중단한다.")
    prov = _augment_provenance(provenance, bars, prep, strategy)

    # 표준화+MLP를 self-contained ONNX 그래프로 내보낸다(Java onnxruntime 인퍼런스용). JSON은 쓰지 않는다.
    onnx_file = Path(out_path).with_suffix(".onnx")
    onnx_file.parent.mkdir(parents=True, exist_ok=True)
    _export_onnx(prep.profile, onnx_file)
    print(
        f"[export_v6] wrote {onnx_file}  (resolved rule_scale={prep.scale!r} "
        f"-> set config evergreen.trading.v6.rule-scale to this value)"
    )

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
        # 골든 신호와 같은 프로파일에서 나온 ONNX 모델을 짝으로 낸다: Java 패리티 테스트가 이 onnx를
        # ONNX Runtime으로 실행해 golden json의 기대 신호를 그대로 재현하는지 검증한다.
        _export_onnx(prep.profile, golden_file.with_suffix(".onnx"))
    return prep.profile


def _augment_provenance(
    provenance: dict[str, Any] | None,
    bars: list[Candle],
    prep: "_Prepared",
    strategy: "Trend2Strategy | None" = None,
) -> dict[str, Any]:
    """재현성 검증을 위해 런타임(numpy/python)·데이터 해시·학습 seed·rule_scale·DL 하이퍼파라미터를 덧붙인다."""
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
    if strategy is not None:
        # 어떤 DL 하이퍼파라미터로 이 모델을 학습했는지 감사(audit)할 수 있게 기록한다.
        merged["dlHyperparams"] = {
            "hidden1": strategy.dl_hidden1,
            "hidden2": strategy.dl_hidden2,
            "lr": strategy.dl_lr,
            "weightDecay": strategy.dl_weight_decay,
            "dropout": strategy.dl_dropout,
            "valFraction": strategy.dl_val_fraction,
            "patience": strategy.dl_patience,
            "maxEpochs": strategy.dl_epochs,
            "minTrainRows": strategy.dl_min_train_rows,
        }
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
    # dynamo exporter 권장 방식: 구식 dynamic_axes 대신 dynamic_shapes로 배치 축을 동적 선언한다.
    # 키는 forward(self, x)의 인자명 'x'. batch>=1 허용(Java=1행, Python=N행 추론). 예시 입력은
    # batch=2로 두어 축이 1로 specialize되지 않게 한다. 출력 배치 동적성은 그래프에서 자동 파생된다.
    batch = torch.export.Dim("batch", min=1)
    torch.onnx.export(
        _StdMLP().eval(),
        (torch.zeros((2, input_dim), dtype=torch.float64),),
        str(out_file),
        input_names=["features"],
        output_names=["probability"],
        dynamic_shapes={"x": {0: batch}},
        opset_version=18,
        dynamo=True,
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


def _bundle_dict(strategy: Trend2Strategy, prep: _Prepared, provenance: dict[str, Any] | None) -> dict[str, Any]:
    """골든 픽스처의 메타데이터 헤더. 모델 가중치는 담지 않는다 — 인퍼런스는 ONNX가 담당한다."""
    bundle: dict[str, Any] = {
        "version": "v6",
        "intervalKey": PREFERRED_INTERVAL_KEY,
        "params": {
            "ruleScale": float(prep.scale),
            "buyCutoff": float(strategy.buy_cutoff),
            "sellCutoff": float(strategy.sell_cutoff),
        },
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
