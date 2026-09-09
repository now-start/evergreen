"""Causal regime inputs with a capacity- and timestamp-matched short-input control."""

import math
from datetime import datetime
from typing import Literal

import torch
from torch import Tensor

from evergreen.market import Candle
from evergreen.research.learning.models import WARMUP, window_features

FeatureSet = Literal["short", "short-matched", "long"]


def feature_warmup(feature_set: FeatureSet) -> int:
    if feature_set not in ("short", "short-matched", "long"):
        raise ValueError("알 수 없는 장세 입력입니다")
    return WARMUP if feature_set == "short" else 200


def feature_channels(feature_set: FeatureSet) -> int:
    feature_warmup(feature_set)
    return 5 if feature_set == "short" else 10


def regime_features(
    candles: list[Candle], feature_set: FeatureSet = "short"
) -> tuple[Tensor, tuple[datetime, ...]]:
    warmup = feature_warmup(feature_set)
    if len(candles) < warmup:
        raise ValueError("장세 입력 준비 구간이 부족합니다")
    base, times = window_features(candles)
    if feature_set == "short":
        return base, times
    base, times = base[warmup - WARMUP :], times[warmup - WARMUP :]
    if feature_set == "short-matched":
        return torch.cat((base, torch.zeros_like(base)), dim=2), times
    closes = [float(c.close) for c in candles]
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    rows = []
    for i in range(168, len(candles)):
        recent = returns[i - 24 : i]
        mean = sum(recent) / 24
        rows.append(
            [math.log(closes[i] / closes[i - hours]) for hours in (24, 72, 168)]
            + [
                math.log((sum(closes[i - 47 : i + 1]) / 48) / (sum(closes[i - 167 : i + 1]) / 168)),
                math.sqrt(sum((value - mean) ** 2 for value in recent) / 24),
            ]
        )
    extra = torch.tensor(rows, dtype=torch.float32).unfold(0, 32, 1).transpose(1, 2)
    features = torch.cat((base, extra), dim=2).contiguous()
    if not torch.isfinite(features).all():
        raise ValueError("장기 장세 입력에 비유한 값이 있습니다")
    return features, times
