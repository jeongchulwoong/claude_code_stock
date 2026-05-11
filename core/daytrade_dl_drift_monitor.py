"""
core/daytrade_dl_drift_monitor.py — paper-only distribution-shift advisory.

매니페스트 phase_3 사양:
  - AE outlier 율 모니터링 (paper journal 로그 only)
  - 진입 결정 영향 0 — log_to_separate_paper_journal_table=True
  - 분포 시프트 알람 (학습 분포와 라이브 분포의 갭 정량화)

설계:
  - 주기적으로 (분/시간 단위) 최근 N건 candidate 의 AE outlier rate 측정
  - 임계 (default 0.20 = 20% 이상이 outlier) 초과 시 alert
  - alert 는 logger 만 — main loop / order 흐름 영향 0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DriftSnapshot:
    """순간 분포 시프트 측정 결과."""
    sample_size: int
    outlier_count: int
    outlier_rate: float
    threshold: float
    is_alert: bool
    avg_reconstruction_error: float
    reason: str = "ok"


class DriftMonitor:
    """AutoEncoder 기반 분포 시프트 모니터.

    paper-only 정책:
      - 진입 결정에 절대 영향 X — 호출자는 advisory 만 사용
      - alert 시 logger 만 갱신, main loop 흐름 변경 X
      - state 는 메모리 only (영구 저장 X) — main 재시작 시 reset

    임계 정책:
      - outlier_rate > threshold (default 0.20): drift alert
      - 학습 시 train AE 의 95th percentile 가 outlier 정의 → 20% 이상이면 분포 변동
    """

    def __init__(
        self,
        ae_artifact: Any | None = None,
        *,
        window_size: int = 50,
        alert_threshold: float = 0.20,
        min_samples_for_alert: int = 30,
    ) -> None:
        """
        Args:
          ae_artifact: joblib.load() 결과 dict {"ae": TrainedAutoEncoder} 또는 None.
                       None 이면 모니터 비활성 (모든 snapshot is_alert=False).
          window_size: 최근 N건 캔디데이트의 outlier rate 측정.
          alert_threshold: outlier rate 가 이 값 초과 시 alert.
          min_samples_for_alert: 표본 부족 시 alert 안 함 (false alarm 방지).
        """
        self._ae = None
        self._scaler = None
        self._mlp = None
        self._feature_columns: list[str] = []
        self._threshold = 0.0
        if ae_artifact is not None:
            try:
                self._ae = ae_artifact.get("ae") if isinstance(ae_artifact, dict) else ae_artifact
                if self._ae is not None:
                    self._scaler = self._ae.pipeline.named_steps["scaler"]
                    self._mlp = self._ae.pipeline.named_steps["ae"]
                    self._feature_columns = list(getattr(self._ae, "feature_columns", []))
                    self._threshold = float(getattr(self._ae, "anomaly_threshold", 0.0))
            except Exception:
                self._ae = None

        self.window_size = int(window_size)
        self.alert_threshold = float(alert_threshold)
        self.min_samples_for_alert = int(min_samples_for_alert)
        self._recent_errors: list[float] = []
        self._recent_outliers: list[bool] = []

    @property
    def available(self) -> bool:
        return self._ae is not None and self._mlp is not None and self._scaler is not None

    def record(self, feature_row: dict[str, Any]) -> DriftSnapshot:
        """단일 candidate 기록 + 현재 outlier rate 반환. 어떤 실패도 raise 안 함."""
        if not self.available:
            return DriftSnapshot(
                sample_size=0, outlier_count=0, outlier_rate=0.0,
                threshold=self.alert_threshold, is_alert=False,
                avg_reconstruction_error=0.0, reason="ae_unavailable",
            )

        try:
            import numpy as np

            row = []
            for col in self._feature_columns:
                v = feature_row.get(col, 0.0) if isinstance(feature_row, dict) else 0.0
                if isinstance(v, bool):
                    row.append(1.0 if v else 0.0)
                elif isinstance(v, (int, float)):
                    f = float(v)
                    if f != f or f in (float("inf"), float("-inf")):
                        f = 0.0
                    row.append(f)
                else:
                    row.append(0.0)
            x = np.asarray(row, dtype=float).reshape(1, -1)
            x_scaled = self._scaler.transform(x)
            x_recon = self._mlp.predict(x_scaled)
            err = float(np.mean((x_scaled - x_recon) ** 2))
            is_outlier = err > self._threshold

            self._recent_errors.append(err)
            self._recent_outliers.append(is_outlier)
            if len(self._recent_errors) > self.window_size:
                self._recent_errors.pop(0)
                self._recent_outliers.pop(0)

            n = len(self._recent_errors)
            n_out = sum(1 for b in self._recent_outliers if b)
            rate = n_out / n if n > 0 else 0.0
            avg_err = sum(self._recent_errors) / n if n > 0 else 0.0
            is_alert = (n >= self.min_samples_for_alert and rate > self.alert_threshold)
            return DriftSnapshot(
                sample_size=n, outlier_count=n_out, outlier_rate=round(rate, 4),
                threshold=self.alert_threshold, is_alert=is_alert,
                avg_reconstruction_error=round(avg_err, 6),
            )
        except Exception as exc:
            return DriftSnapshot(
                sample_size=len(self._recent_errors), outlier_count=0,
                outlier_rate=0.0, threshold=self.alert_threshold, is_alert=False,
                avg_reconstruction_error=0.0,
                reason=f"record_failed:{type(exc).__name__}",
            )

    def reset(self) -> None:
        self._recent_errors.clear()
        self._recent_outliers.clear()

    def state(self) -> dict[str, Any]:
        """현재 모니터 상태 (디버그/대시보드용)."""
        n = len(self._recent_errors)
        return {
            "available": self.available,
            "window_size": self.window_size,
            "current_sample_count": n,
            "outlier_count": sum(1 for b in self._recent_outliers if b),
            "outlier_rate": (sum(1 for b in self._recent_outliers if b) / n) if n > 0 else 0.0,
            "alert_threshold": self.alert_threshold,
            "ae_threshold": self._threshold,
        }
