"""
core/daytrade_dl_autoencoder.py — AutoEncoder anomaly filter (advisory, paper-only).

매니페스트 phase_3 rank 1 (lowest risk DL):
  - label-free outlier scoring via reconstruction error
  - CPU-trainable (sklearn MLPRegressor 사용 — PyTorch 부재 fallback)
  - advisory only — 진입 결정 영향 0
  - threshold = train set 의 95-percentile reconstruction error

reference: Bergmann et al. 2019 — reconstruction-error-based outlier detection.

사용 시나리오:
  - paper journal 에 'ae_anomaly_score' / 'ae_is_outlier' 기록
  - 분포 시프트 모니터링 (시간이 흐르며 outlier rate 변화 추적)
  - 운영 게이트 활용은 매니페스트 phase_5 통과 후에만
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.neural_network import MLPRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass
class AutoEncoderConfig:
    bottleneck_size: int = 16
    encoder_layers: tuple[int, ...] = (128, 64, 32)
    activation: str = "relu"
    alpha: float = 1e-4
    max_iter: int = 200
    early_stopping: bool = True
    validation_fraction: float = 0.15
    n_iter_no_change: int = 10
    random_state: int = 42
    anomaly_percentile: float = 95.0   # train 95-percentile 을 outlier 임계로


@dataclass
class TrainedAutoEncoder:
    pipeline: Pipeline
    feature_columns: list[str]
    anomaly_threshold: float
    train_error_stats: dict[str, float]
    config: AutoEncoderConfig


def _build_mlp(cfg: AutoEncoderConfig) -> MLPRegressor:
    layers = list(cfg.encoder_layers) + [cfg.bottleneck_size] + list(reversed(cfg.encoder_layers))
    return MLPRegressor(
        hidden_layer_sizes=tuple(layers),
        activation=cfg.activation,
        alpha=cfg.alpha,
        max_iter=cfg.max_iter,
        early_stopping=cfg.early_stopping,
        validation_fraction=cfg.validation_fraction,
        n_iter_no_change=cfg.n_iter_no_change,
        random_state=cfg.random_state,
    )


def build_autoencoder_pipeline(cfg: AutoEncoderConfig | None = None) -> Pipeline:
    """StandardScaler → MLPRegressor.

    중요: 입력/타겟 모두 같은 scaler 로 표준화한 뒤 학습 → reconstruction error 가
    scaled space (mean=0, std=1) 에서 측정되어 수치 안정. scaler 는 별도 보관 X
    (Pipeline 안에서 input 만 변환).
    """
    cfg = cfg or AutoEncoderConfig()
    return Pipeline([
        ("scaler", StandardScaler()),
        ("ae", _build_mlp(cfg)),
    ])


def fit_autoencoder(
    X: np.ndarray,
    feature_columns: list[str],
    cfg: AutoEncoderConfig | None = None,
) -> TrainedAutoEncoder:
    """AE 학습 + anomaly threshold 결정.

    학습 절차:
      1) scaler 로 X 표준화 → X_scaled
      2) MLP 가 X_scaled → X_scaled 학습 (target 도 scaled)
      3) reconstruction error = MSE in scaled space → 수치 안정
    """
    cfg = cfg or AutoEncoderConfig()
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    mlp = _build_mlp(cfg)
    mlp.fit(X_scaled, X_scaled)   # 둘 다 scaled space

    # train set reconstruction error 분포 (scaled space)
    X_recon_scaled = mlp.predict(X_scaled)
    errors = np.mean((X_scaled - X_recon_scaled) ** 2, axis=1)
    threshold = float(np.percentile(errors, cfg.anomaly_percentile))

    # Pipeline 으로 wrap — predict() 시 scaler + mlp 둘 다 적용
    pipeline = Pipeline([
        ("scaler", scaler),
        ("ae", mlp),
    ])

    return TrainedAutoEncoder(
        pipeline=pipeline,
        feature_columns=list(feature_columns),
        anomaly_threshold=threshold,
        train_error_stats={
            "min":  float(errors.min()),
            "max":  float(errors.max()),
            "mean": float(errors.mean()),
            "p50":  float(np.percentile(errors, 50)),
            "p90":  float(np.percentile(errors, 90)),
            "p95":  float(np.percentile(errors, 95)),
            "p99":  float(np.percentile(errors, 99)),
        },
        config=cfg,
    )


@dataclass(frozen=True)
class AnomalyScore:
    available: bool
    reconstruction_error: float | None
    is_outlier: bool
    threshold: float | None
    reason: str = "ok"


def score_anomaly(ae: TrainedAutoEncoder, feature_row: np.ndarray) -> AnomalyScore:
    """단일 row 의 reconstruction error + outlier 판정. 어떤 실패도 raise X.

    error 계산은 scaled space — 학습 시 사용한 동일 scaler 로 변환 후 MSE.
    """
    try:
        if feature_row.ndim == 1:
            x = feature_row.reshape(1, -1)
        else:
            x = feature_row
        scaler = ae.pipeline.named_steps["scaler"]
        mlp = ae.pipeline.named_steps["ae"]
        x_scaled = scaler.transform(x)
        x_recon = mlp.predict(x_scaled)
        err = float(np.mean((x_scaled - x_recon) ** 2))
        return AnomalyScore(
            available=True,
            reconstruction_error=err,
            is_outlier=err > ae.anomaly_threshold,
            threshold=ae.anomaly_threshold,
        )
    except Exception as exc:
        return AnomalyScore(
            available=False,
            reconstruction_error=None,
            is_outlier=False,
            threshold=None,
            reason=f"predict_failed:{type(exc).__name__}",
        )
