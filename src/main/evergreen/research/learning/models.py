"""Causal chart features and small, offline PyTorch classifiers."""

import hashlib
import math
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, cast

import torch
from pydantic import BaseModel, ConfigDict, Field
from torch import Tensor, nn

from evergreen.market import Candle, validate_candles, write_json

ARCHITECTURES = ("mlp-v1", "cnn-v1", "linear-v1")
LOOKBACK = 32
HORIZON = 12
WARMUP = 55
FeatureSet = Literal["short", "short-200", "breakout-context"]


def feature_warmup(feature_set: FeatureSet) -> int:
    if feature_set not in ("short", "short-200", "breakout-context"):
        raise ValueError("지원하지 않는 입력 특성입니다")
    return WARMUP if feature_set == "short" else 200


LABEL_THRESHOLD = math.log((1.001 * 1.0005) / (0.999 * 0.9995)) + 0.002


@dataclass(frozen=True)
class Samples:
    features: Tensor  # (sample, 32 closed hours, 5 features)
    labels: Tensor  # (sample,), binary net-forward-return proxy
    signal_times: tuple[datetime, ...]
    label_times: tuple[datetime, ...]


def window_features(
    candles: list[Candle], feature_set: FeatureSet = "short"
) -> tuple[Tensor, tuple[datetime, ...]]:
    warmup = feature_warmup(feature_set)
    if len(candles) < warmup:
        raise ValueError(f"모델 입력에는 연속된 확정 봉 {warmup}개 이상이 필요합니다")
    ordered, quality = validate_candles(candles, candles[0].open_time, candles[-1].close_time)
    if not quality.valid or quality.duplicates or quality.incomplete or ordered != candles:
        raise ValueError("학습 입력의 정렬·연속성·확정 봉 품질 검사가 실패했습니다")
    closes = [float(bar.close) for bar in candles]
    rows = [
        [
            math.log(closes[i] / closes[i - 1]),
            math.log(float(bar.high / bar.low)),
            math.log(float(bar.close / bar.open)),
            math.log1p(float(bar.volume)) - math.log1p(float(candles[i - 1].volume)),
            closes[i] / (sum(closes[i - 23 : i + 1]) / 24) - 1,
        ]
        for i, bar in enumerate(candles)
        if i >= 23
    ]
    if feature_set == "breakout-context":
        highs = [float(bar.high) for bar in candles]
        lows = [float(bar.low) for bar in candles]
        volumes = [float(bar.volume) for bar in candles]
        returns = [0.0] + [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
        rows = []
        for i in range(168, len(candles)):
            recent = returns[i - 23 : i + 1]
            average = sum(recent) / 24
            rows.append(
                [
                    math.log(closes[i] / max(highs[i - 168 : i])),
                    math.log(closes[i] / min(lows[i - 48 : i])),
                    math.log(
                        (sum(closes[i - 47 : i + 1]) / 48) / (sum(closes[i - 167 : i + 1]) / 168)
                    ),
                    math.log1p(volumes[i]) - math.log1p(sum(volumes[i - 24 : i]) / 24),
                    math.sqrt(sum((value - average) ** 2 for value in recent) / 24),
                ]
            )
    features = torch.tensor(rows, dtype=torch.float32)
    if not torch.isfinite(features).all():
        raise ValueError("모델 특성에 비유한 값이 있습니다")
    windows = features.unfold(0, LOOKBACK, 1).transpose(1, 2).contiguous()
    if feature_set == "short-200":
        windows = windows[200 - WARMUP :]
    return windows, tuple(bar.close_time for bar in candles[warmup - 1 :])


def make_samples(candles: list[Candle]) -> Samples:
    windows, times = window_features(candles)
    size = len(windows) - HORIZON - 1
    if size <= 0:
        raise ValueError("미래 레이블 경계를 제거한 후 학습 표본이 없습니다")
    indices = range(WARMUP - 1, len(candles) - HORIZON - 1)
    labels = [
        float(
            math.log(float(candles[i + HORIZON + 1].open / candles[i + 1].open)) > LABEL_THRESHOLD
        )
        for i in indices
    ]
    return Samples(
        windows[:size],
        torch.tensor(labels, dtype=torch.float32),
        times[:size],
        tuple(candles[i + HORIZON + 1].open_time for i in indices),
    )


class ChartModel(nn.Module):
    def __init__(self, architecture: str) -> None:
        super().__init__()
        if architecture not in ARCHITECTURES:
            raise ValueError("지원하지 않는 모델 구조입니다")
        self.architecture = architecture
        if architecture == "mlp-v1":
            self.layers = nn.Sequential(
                nn.Flatten(),
                nn.Linear(160, 32),
                nn.ReLU(),
                nn.Linear(32, 16),
                nn.ReLU(),
                nn.Linear(16, 1),
            )
        elif architecture == "linear-v1":
            self.layers = nn.Sequential(nn.Flatten(), nn.Linear(160, 1))
        else:
            self.layers = nn.Sequential(
                nn.Conv1d(5, 16, 3, padding=1),
                nn.ReLU(),
                nn.Conv1d(16, 16, 3, padding=1),
                nn.ReLU(),
                nn.AdaptiveAvgPool1d(1),
                nn.Flatten(),
                nn.Linear(16, 1),
            )

    def forward(self, x: Tensor) -> Tensor:
        # x: (batch, time=32, feature=5); output: one logit per sample.
        if self.architecture == "cnn-v1":
            x = x.transpose(1, 2)
        return cast(Tensor, self.layers(x)).squeeze(-1)


Finite = Annotated[float, Field(allow_inf_nan=False)]
Scale = Annotated[float, Field(gt=0, allow_inf_nan=False)]


class ModelMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid")
    architecture: Literal["mlp-v1", "cnn-v1", "linear-v1"]
    mean: list[Finite] = Field(min_length=5, max_length=5)
    scale: list[Scale] = Field(min_length=5, max_length=5)
    checkpoint_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    epochs: int = Field(gt=0)
    seed: int
    training_samples: int = Field(gt=0)
    positive_rate: Finite = Field(ge=0, le=1)
    losses: list[Finite]
    torch_version: str
    device: str
    max_epochs: int | None = Field(default=None, gt=0)
    validation_samples: int = Field(default=0, ge=0)
    validation_losses: list[Finite] = Field(default_factory=list)
    best_epoch: int | None = Field(default=None, gt=0)
    checkpoint_epoch: int | None = Field(default=None, gt=0)
    patience: int | None = Field(default=None, gt=0)
    stopped_early: bool = False
    loss_weighting: Literal["uniform", "sample"] = "uniform"
    feature_set: FeatureSet = "short"


@dataclass
class TrainedModel:
    model: ChartModel
    mean: Tensor
    scale: Tensor
    metadata: ModelMetadata

    def probabilities(self, features: Tensor) -> Tensor:
        normalized = ((features - self.mean) / self.scale).clamp(-10, 10)
        device = next(self.model.parameters()).device
        self.model.eval()
        with torch.inference_mode():
            probabilities = cast(Tensor, self.model(normalized.to(device))).sigmoid().cpu()
        if not torch.isfinite(probabilities).all():
            raise ValueError("모델 예측이 유효하지 않습니다")
        return probabilities

    def predict(self, candles: list[Candle]) -> dict[datetime, Decimal]:
        features, times = window_features(candles, self.metadata.feature_set)
        values = self.probabilities(features).tolist()
        return {time: Decimal(str(value)) for time, value in zip(times, values, strict=True)}


def validation_loss(
    model: ChartModel, features: Tensor, labels: Tensor, weights: Tensor | None = None
) -> float:
    with torch.inference_mode():
        return float(
            nn.functional.binary_cross_entropy_with_logits(
                model(features), labels, weight=weights
            ).item()
        )


def _normalize_weights(weights: Tensor | None, labels: Tensor) -> Tensor | None:
    if weights is None:
        return None
    if (
        weights.shape != labels.shape
        or not torch.isfinite(weights).all()
        or (weights < 0).any()
        or weights.sum() <= 0
    ):
        raise ValueError("학습·검증 가중치의 차원·값·합계가 잘못됐습니다")
    detached = weights.detach().to(dtype=torch.float32, device="cpu")
    average = detached.mean()
    if not torch.isfinite(average) or average <= 0:
        raise ValueError("가중치 평균이 유한한 양수가 아닙니다")
    normalized = detached / average
    if not torch.isfinite(normalized).all():
        raise ValueError("정규화된 가중치가 비유한 값입니다")
    return normalized


def _merge_samples(segments: list[Samples]) -> tuple[Tensor, Tensor]:
    if not segments or any(len(sample.labels) == 0 for sample in segments):
        raise ValueError("학습·검증 표본이 비어 있습니다")
    for sample in segments:
        if len(sample.signal_times) != len(sample.labels) or len(sample.label_times) != len(
            sample.labels
        ):
            raise ValueError("표본 시각과 레이블 수가 다릅니다")
    x = torch.cat([sample.features for sample in segments])
    y = torch.cat([sample.labels for sample in segments])
    if x.shape != (len(y), LOOKBACK, 5) or not torch.isfinite(x).all():
        raise ValueError("학습·검증 입력 차원 또는 값이 잘못됐습니다")
    if not torch.all((y == 0) | (y == 1)):
        raise ValueError("학습·검증 레이블은 0 또는 1이어야 합니다")
    return x, y


def train_model(
    segments: list[Samples],
    architecture: str,
    output: Path,
    *,
    epochs: int = 20,
    device: str = "cpu",
    validation: list[Samples] | None = None,
    patience: int | None = None,
    seed: int = 17,
    sample_weights: Tensor | None = None,
    validation_weights: Tensor | None = None,
    feature_set: FeatureSet = "short",
) -> TrainedModel:
    feature_warmup(feature_set)
    if architecture not in ARCHITECTURES or not segments or epochs <= 0:
        raise ValueError("모델·학습 구간·epoch 설정이 잘못됐습니다")
    if not 0 <= seed < 2**32:
        raise ValueError("학습 시드가 잘못됐습니다")
    if patience is not None and (patience <= 0 or not validation):
        raise ValueError("조기 종료에는 검증 표본과 양수 patience가 필요합니다")
    x, y = _merge_samples(segments)
    validation_data = _merge_samples(validation) if validation is not None else None
    if (validation_weights is not None and (sample_weights is None or validation_data is None)) or (
        sample_weights is not None and validation_data is not None and validation_weights is None
    ):
        raise ValueError("가중 학습의 검증 가중치 설정이 일치하지 않습니다")
    weights = _normalize_weights(sample_weights, y)
    vweights = (
        _normalize_weights(validation_weights, validation_data[1])
        if validation_data is not None
        else None
    )
    if validation is not None and max(t for sample in segments for t in sample.label_times) >= min(
        t for sample in validation for t in sample.signal_times
    ):
        raise ValueError("학습 레이블과 검증 신호의 시간 경계가 겹칩니다")
    output.mkdir(parents=True, exist_ok=False)
    torch.manual_seed(seed)
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    mean = x.mean(dim=(0, 1))
    scale = x.std(dim=(0, 1), correction=0).clamp_min(1e-8)
    x = ((x - mean) / scale).clamp(-10, 10).to(device)
    y = y.to(device)
    if weights is not None:
        weights = weights.to(device)
    if vweights is not None:
        vweights = vweights.to(device)
    if validation_data is not None:
        vx, vy = validation_data
        validation_data = (((vx - mean) / scale).clamp(-10, 10).to(device), vy.to(device))
    model = ChartModel(architecture).to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=0.0001)
    criterion = nn.BCEWithLogitsLoss()
    losses: list[float] = []
    validation_losses: list[float] = []
    best_loss = math.inf
    best_epoch: int | None = None
    best_weights: dict[str, Tensor] | None = None
    best_optimizer: dict[str, object] | None = None
    stale_epochs = 0
    for epoch in range(1, epochs + 1):
        model.train()
        permutation = torch.randperm(len(y), device=device)
        total = 0.0
        for offset in range(0, len(y), 128):
            indices = permutation[offset : offset + 128]
            optimizer.zero_grad(set_to_none=True)
            logits = model(x[indices])
            loss = (
                criterion(logits, y[indices])
                if weights is None
                else nn.functional.binary_cross_entropy_with_logits(
                    logits, y[indices], weight=weights[indices]
                )
            )
            if not torch.isfinite(loss):
                raise ValueError("학습 손실이 비유한 값입니다")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
            optimizer.step()
            total += loss.item() * len(indices)
        losses.append(total / len(y))
        if validation_data is not None:
            model.eval()
            measured = (
                validation_loss(model, *validation_data)
                if vweights is None
                else validation_loss(model, *validation_data, vweights)
            )
            if not math.isfinite(measured):
                raise ValueError("검증 손실이 비유한 값입니다")
            validation_losses.append(measured)
            if measured < best_loss:
                best_loss, best_epoch, stale_epochs = measured, epoch, 0
                if patience is not None:
                    best_weights = {
                        key: value.detach().clone() for key, value in model.state_dict().items()
                    }
                    best_optimizer = deepcopy(optimizer.state_dict())
            else:
                stale_epochs += 1
            if patience is not None and stale_epochs >= patience:
                break
    checkpoint_epoch = len(losses)
    if best_weights is not None and best_optimizer is not None:
        model.load_state_dict(best_weights)
        optimizer.load_state_dict(best_optimizer)
        checkpoint_epoch = best_epoch or len(losses)
    checkpoint = output / "checkpoint.pt"
    torch.save(
        {
            "model": model.state_dict(),
            "optimizer": optimizer.state_dict(),
            "epoch": checkpoint_epoch,
        },
        checkpoint,
    )
    metadata = ModelMetadata.model_validate(
        {
            "architecture": architecture,
            "feature_set": feature_set,
            "mean": mean.tolist(),
            "scale": scale.tolist(),
            "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            "epochs": len(losses),
            "max_epochs": epochs,
            "validation_samples": len(validation_data[1]) if validation_data is not None else 0,
            "validation_losses": validation_losses,
            "best_epoch": best_epoch,
            "checkpoint_epoch": checkpoint_epoch,
            "patience": patience,
            "stopped_early": len(losses) < epochs,
            "seed": seed,
            "training_samples": len(y),
            "positive_rate": y.mean().item(),
            "losses": losses,
            "torch_version": str(torch.__version__),
            "device": device,
            "loss_weighting": "sample" if weights is not None else "uniform",
        }
    )
    write_json(output / "model.json", metadata.model_dump(mode="json"))
    return TrainedModel(model, mean, scale, metadata)


def load_model(directory: Path) -> TrainedModel:
    metadata = ModelMetadata.model_validate_json((directory / "model.json").read_text())
    checkpoint = directory / "checkpoint.pt"
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != metadata.checkpoint_sha256:
        raise ValueError("모델 체크포인트 해시가 일치하지 않습니다")
    state = torch.load(checkpoint, map_location="cpu", weights_only=True)
    model = ChartModel(metadata.architecture)
    model.load_state_dict(state["model"], strict=True)
    return TrainedModel(model, torch.tensor(metadata.mean), torch.tensor(metadata.scale), metadata)
