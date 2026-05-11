"""
core/daytrade_dl_deep_mlp.py — Deep MLP (다층 feedforward) for daytrade entry scoring.

매니페스트 phase_3 사양:
  - paper-only (`use_as_entry_gate=False` 강제)
  - artifact path 는 `models/research/dl_` prefix
  - main loop 의 RF entry decision 을 절대 대체 X
  - paper journal 별도 테이블에 로그 (현재 paper_only 라 main loop 영향 0)

⚠️ 매니페스트 explicit_not_recommended 에 "Tabular MLP" 가 있음 — RF 대비 일관된 edge 없다는
연구 (Israel-Kelly-Moskowitz 2020) 인용. 본 모듈은 그럼에도 PyTorch 부재로 fallback 으로 구현.
LSTM/GRU 가용 시 그쪽으로 마이그레이션 필요.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sklearn.calibration import CalibratedClassifierCV
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


@dataclass
class DeepMLPConfig:
    hidden_layer_sizes: tuple[int, ...] = (256, 128, 64, 32)
    activation: str = "relu"
    solver: str = "adam"
    alpha: float = 1e-4
    batch_size: int = 256
    learning_rate_init: float = 1e-3
    max_iter: int = 200
    early_stopping: bool = True
    validation_fraction: float = 0.15
    n_iter_no_change: int = 10
    random_state: int = 42
    calibration_method: str | None = "sigmoid"
    calibration_cv: int = 3


def build_deep_mlp_pipeline(cfg: DeepMLPConfig | None = None) -> Pipeline:
    """StandardScaler → MLPClassifier 파이프라인.

    sklearn MLP 는 입력 정규화에 매우 민감 → StandardScaler 필수.
    sigmoid calibration 으로 확률 신뢰도 보정 (RF 와 동일 정책).
    """
    cfg = cfg or DeepMLPConfig()
    mlp = MLPClassifier(
        hidden_layer_sizes=cfg.hidden_layer_sizes,
        activation=cfg.activation,
        solver=cfg.solver,
        alpha=cfg.alpha,
        batch_size=cfg.batch_size,
        learning_rate_init=cfg.learning_rate_init,
        max_iter=cfg.max_iter,
        early_stopping=cfg.early_stopping,
        validation_fraction=cfg.validation_fraction,
        n_iter_no_change=cfg.n_iter_no_change,
        random_state=cfg.random_state,
    )
    if cfg.calibration_method:
        clf = CalibratedClassifierCV(mlp, method=cfg.calibration_method, cv=cfg.calibration_cv)
    else:
        clf = mlp
    return Pipeline([
        ("scaler", StandardScaler()),
        ("clf", clf),
    ])


@dataclass(frozen=True)
class DeepMLPPrediction:
    available: bool
    tp_prob: float | None
    sl_prob: float | None
    confidence: float | None
    reason: str = "ok"


def predict_deep_mlp(
    pipeline: Pipeline,
    feature_row: np.ndarray,
    *,
    feature_columns: list[str] | None = None,
) -> DeepMLPPrediction:
    """단일 row 점수화. 어떤 실패도 raise 하지 않는다 (main-loop safe)."""
    try:
        if feature_row.ndim == 1:
            x = feature_row.reshape(1, -1)
        else:
            x = feature_row
        proba = pipeline.predict_proba(x)
        if proba.shape[1] < 2:
            return DeepMLPPrediction(False, None, None, None, "binary_output_expected")
        sl_prob = float(proba[0, 0])
        tp_prob = float(proba[0, 1])
        return DeepMLPPrediction(True, tp_prob, sl_prob, max(tp_prob, sl_prob), "ok")
    except Exception as exc:
        return DeepMLPPrediction(False, None, None, None, f"predict_failed:{type(exc).__name__}")
