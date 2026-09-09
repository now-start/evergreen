"""Three-class return regime MLP, purged labels and validation-only early stopping."""

import hashlib
import json
import math
from copy import deepcopy
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import cast

import torch
from torch import Tensor, nn

from evergreen.market import Candle, write_json
from evergreen.research.learning.models import Samples
from evergreen.research.learning.regime_features import (
    FeatureSet,
    feature_channels,
    feature_warmup,
    regime_features,
)

HORIZON = 24


def sample_identity(samples: list[Samples]) -> str:
    rows = [
        (signal.isoformat(), label.isoformat(), int(value))
        for sample in samples
        for signal, label, value in zip(
            sample.signal_times, sample.label_times, sample.labels, strict=True
        )
    ]
    return hashlib.sha256(json.dumps(rows).encode()).hexdigest()


def make_regime_samples(
    candles: list[Candle], horizon: int = HORIZON, feature_set: FeatureSet = "short"
) -> Samples:
    if horizon not in (24, 72):
        raise ValueError("장세 예측 기간은 24 또는 72시간이어야 합니다")
    features, times = regime_features(candles, feature_set)
    size = len(features) - horizon - 1
    if size <= 0:
        raise ValueError("장세 학습 표본이 부족합니다")
    returns = [math.log(float(b.close / a.close)) for a, b in pairwise(candles)]
    labels: list[int] = []
    label_times: list[datetime] = []
    for i in range(feature_warmup(feature_set) - 1, len(candles) - horizon - 1):
        past = returns[i - 24 : i]
        mean = sum(past) / 24
        volatility = math.sqrt(sum((value - mean) ** 2 for value in past) / 24)
        threshold = max(0.003, volatility * math.sqrt(horizon))
        forward = math.log(float(candles[i + horizon + 1].open / candles[i + 1].open))
        labels.append(2 if forward > threshold else 0 if forward < -threshold else 1)
        label_times.append(candles[i + horizon + 1].open_time)
    return Samples(
        features[:size], torch.tensor(labels, dtype=torch.long), times[:size], tuple(label_times)
    )


def split_samples(samples: Samples, start: datetime, end: datetime) -> Samples:
    indices = [
        i
        for i, (signal, label) in enumerate(
            zip(samples.signal_times, samples.label_times, strict=True)
        )
        if start <= signal < end and label < end
    ]
    if not indices:
        raise ValueError("분할 경계 제거 후 표본이 없습니다")
    return Samples(
        samples.features[indices],
        samples.labels[indices],
        tuple(samples.signal_times[i] for i in indices),
        tuple(samples.label_times[i] for i in indices),
    )


class RegimeModel(nn.Module):
    def __init__(self, channels: int = 5) -> None:
        super().__init__()
        self.layers = nn.Sequential(
            nn.Flatten(),
            nn.Linear(32 * channels, 32),
            nn.ReLU(),
            nn.Linear(32, 16),
            nn.ReLU(),
            nn.Linear(16, 3),
        )

    def forward(self, x: Tensor) -> Tensor:
        return cast(Tensor, self.layers(x))


def classification(labels: Tensor, probabilities: Tensor, majority: int) -> dict[str, object]:
    predicted = probabilities.argmax(dim=1)
    matrix = torch.bincount(labels * 3 + predicted, minlength=9).reshape(3, 3)
    scores = [
        2 * int(matrix[i, i]) / max(1, int(matrix[i].sum() + matrix[:, i].sum())) for i in range(3)
    ]
    return {
        "confusion_matrix": matrix.tolist(),
        "macro_f1": sum(scores) / 3,
        "precision": [int(matrix[i, i]) / max(1, int(matrix[:, i].sum())) for i in range(3)],
        "recall": [int(matrix[i, i]) / max(1, int(matrix[i].sum())) for i in range(3)],
        "accuracy": float((labels == predicted).float().mean()),
        "majority_accuracy": float((labels == majority).float().mean()),
        "label_counts": torch.bincount(labels, minlength=3).tolist(),
        "prediction_counts": torch.bincount(predicted, minlength=3).tolist(),
    }


class TrainedRegime:
    def __init__(
        self,
        model: RegimeModel,
        mean: Tensor,
        scale: Tensor,
        majority: int,
        feature_set: FeatureSet = "short",
    ) -> None:
        self.model, self.mean, self.scale, self.majority = model, mean, scale, majority
        self.feature_set = feature_set

    def probabilities(self, features: Tensor) -> Tensor:
        self.model.eval()
        with torch.inference_mode():
            output = cast(
                Tensor, self.model(((features - self.mean) / self.scale).clamp(-10, 10))
            ).softmax(dim=1)
        if not torch.isfinite(output).all():
            raise ValueError("장세 확률에 비유한 값이 있습니다")
        return output

    def predict(self, candles: list[Candle]) -> tuple[dict[datetime, int], Tensor]:
        features, times = regime_features(candles, self.feature_set)
        probabilities = self.probabilities(features)
        confidence, labels = probabilities.max(dim=1)
        return {
            time: int(label) if float(score) >= 0.55 else -1
            for time, label, score in zip(times, labels, confidence, strict=True)
        }, probabilities


def train_regime(
    training: list[Samples],
    validation: Samples,
    output: Path,
    *,
    epochs: int = 100,
    patience: int = 10,
    balanced: bool = False,
    seed: int = 17,
    feature_set: FeatureSet = "short",
) -> TrainedRegime:
    channels = feature_channels(feature_set)
    if not training or epochs <= 0 or patience <= 0 or not 0 <= seed < 2**32:
        raise ValueError("학습 설정이 잘못됐습니다")
    if max(t for sample in training for t in sample.label_times) >= min(validation.signal_times):
        raise ValueError("학습 레이블이 검증 경계를 침범했습니다")
    x = torch.cat([s.features for s in training])
    y = torch.cat([s.labels for s in training])
    if x.shape != (len(y), 32, channels) or validation.features.shape != (
        len(validation.labels),
        32,
        channels,
    ):
        raise ValueError("장세 입력 차원이 잘못됐습니다")
    if not torch.isfinite(x).all() or not torch.isfinite(validation.features).all():
        raise ValueError("장세 입력 값이 잘못됐습니다")
    if set(y.tolist()) != {0, 1, 2} or set(validation.labels.tolist()) != {0, 1, 2}:
        raise ValueError("학습·검증에 세 장세가 모두 필요합니다")
    output.mkdir(parents=True, exist_ok=False)
    torch.manual_seed(seed)
    torch.set_num_threads(2)
    torch.use_deterministic_algorithms(True)
    mean, scale = x.mean(dim=(0, 1)), x.std(dim=(0, 1), correction=0).clamp_min(1e-8)
    normalized = ((x - mean) / scale).clamp(-10, 10)
    vx = ((validation.features - mean) / scale).clamp(-10, 10)
    model = RegimeModel(channels)
    optimizer = torch.optim.Adam(model.parameters(), lr=0.001, weight_decay=0.0001)
    class_weights = len(y) / (3 * torch.bincount(y, minlength=3).float()) if balanced else None
    best, best_epoch, stale = math.inf, 0, 0
    weights: dict[str, Tensor] | None = None
    history: list[dict[str, float | int]] = []
    for epoch in range(1, epochs + 1):
        model.train()
        permutation = torch.randperm(len(y))
        total = 0.0
        for offset in range(0, len(y), 256):
            indices = permutation[offset : offset + 256]
            optimizer.zero_grad(set_to_none=True)
            loss = nn.functional.cross_entropy(
                model.forward(normalized[indices]), y[indices], weight=class_weights
            )
            if not torch.isfinite(loss):
                raise ValueError("학습 손실이 비유한 값입니다")
            loss.backward()  # type: ignore[no-untyped-call]  # PyTorch backward lacks annotations.
            optimizer.step()
            # PyTorch's weighted mean divides by the sum of sample weights, not batch size.
            denominator = (
                float(class_weights[y[indices]].sum())
                if class_weights is not None
                else len(indices)
            )
            total += float(loss.detach()) * denominator
        model.eval()
        with torch.inference_mode():
            loss_value = float(nn.functional.cross_entropy(model(vx), validation.labels))
        if not math.isfinite(loss_value):
            raise ValueError("검증 손실이 비유한 값입니다")
        history.append(
            {"epoch": epoch, "train_loss": total / len(y), "validation_loss": loss_value}
        )
        if loss_value < best:
            best, best_epoch, stale, weights = loss_value, epoch, 0, deepcopy(model.state_dict())
        else:
            stale += 1
        if stale >= patience:
            break
    if weights is None:
        raise ValueError("복원할 최적 모델이 없습니다")
    model.load_state_dict(weights)
    majority = int(torch.bincount(y, minlength=3).argmax())
    trained = TrainedRegime(model, mean, scale, majority, feature_set)
    checkpoint = output / "weights.pt"
    torch.save({"state_dict": weights, "mean": mean, "scale": scale}, checkpoint)
    write_json(
        output / "training.json",
        {
            "class_order": ["down", "sideways", "up"],
            "seed": seed,
            "feature_set": feature_set,
            "feature_channels": channels,
            "feature_warmup": feature_warmup(feature_set),
            "horizon_hours": (
                training[0].label_times[0] - training[0].signal_times[0]
            ).total_seconds()
            / 3600,
            "max_epochs": epochs,
            "patience": patience,
            "class_weights": class_weights.tolist() if class_weights is not None else None,
            "validation_objective": "unweighted_cross_entropy",
            "best_epoch": best_epoch,
            "history": history,
            "torch_version": str(torch.__version__),
            "device": "cpu",
            "training_samples": len(y),
            "validation_samples": len(validation.labels),
            "training_sample_sha256": sample_identity(training),
            "validation_sample_sha256": sample_identity([validation]),
            "label_counts": torch.bincount(y, minlength=3).tolist(),
            "training_label_max": max(t for s in training for t in s.label_times).isoformat(),
            "validation_signal_min": min(validation.signal_times).isoformat(),
            "validation_label_max": max(validation.label_times).isoformat(),
            "mean": mean.tolist(),
            "scale": scale.tolist(),
            "checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
            "validation_metrics": classification(
                validation.labels, trained.probabilities(validation.features), majority
            ),
        },
    )
    return trained


def load_regime(directory: Path, evaluation_start: datetime) -> TrainedRegime:
    """Load a local frozen checkpoint only if all training/selection labels predate evaluation."""
    metadata = json.loads((directory / "training.json").read_text())
    feature_set = metadata.get("feature_set", "short")
    channels = feature_channels(feature_set)
    if evaluation_start.utcoffset() is None or any(
        datetime.fromisoformat(metadata[key]) >= evaluation_start
        for key in ("training_label_max", "validation_label_max")
    ):
        raise ValueError("고정 모델의 학습·검증 경계가 평가를 침범했습니다")
    checkpoint = directory / "weights.pt"
    if hashlib.sha256(checkpoint.read_bytes()).hexdigest() != metadata["checkpoint_sha256"]:
        raise ValueError("고정 모델 해시 불일치")
    weights = torch.load(checkpoint, map_location="cpu", weights_only=True)
    mean, scale = weights["mean"], weights["scale"]
    if (
        mean.shape != (channels,)
        or scale.shape != (channels,)
        or not torch.isfinite(mean).all()
        or not torch.isfinite(scale).all()
        or not (scale > 0).all()
    ):
        raise ValueError("고정 모델 정규화 값이 잘못됐습니다")
    model = RegimeModel(channels)
    model.load_state_dict(weights["state_dict"], strict=True)
    majority = max(range(3), key=lambda i: metadata["label_counts"][i])
    return TrainedRegime(model, mean, scale, majority, feature_set)
