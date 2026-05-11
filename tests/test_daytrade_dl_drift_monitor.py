"""tests/test_daytrade_dl_drift_monitor.py — DriftMonitor 단위 테스트.

main / order_manager / risk_manager 를 import 하지 않는다.
모니터가 진입 결정에 영향 0 — record() 호출이 절대 raise 하지 않음을 회귀 차단.
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np

ROOT = pathlib.Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.daytrade_dl_autoencoder import AutoEncoderConfig, fit_autoencoder  # noqa: E402
from core.daytrade_dl_drift_monitor import DriftMonitor, DriftSnapshot  # noqa: E402


def _train_tiny_ae(n_samples: int = 200, n_features: int = 8, seed: int = 42):
    rng = np.random.default_rng(seed)
    # Two clusters — center 0 and center 5
    X = rng.normal(0.0, 1.0, size=(n_samples, n_features))
    feature_cols = [f"f{i}" for i in range(n_features)]
    cfg = AutoEncoderConfig(bottleneck_size=4, max_iter=50, random_state=seed)
    return fit_autoencoder(X, feature_cols, cfg), feature_cols


def test_monitor_unavailable_when_no_ae():
    mon = DriftMonitor(ae_artifact=None)
    assert mon.available is False
    snap = mon.record({"f0": 1.0})
    assert snap.is_alert is False
    assert snap.reason == "ae_unavailable"
    assert snap.sample_size == 0


def test_monitor_record_inlier_no_alert():
    ae, cols = _train_tiny_ae()
    mon = DriftMonitor({"ae": ae}, window_size=50, alert_threshold=0.20, min_samples_for_alert=20)
    # In-distribution rows (centered around 0)
    rng = np.random.default_rng(7)
    for _ in range(30):
        row = {c: float(rng.normal(0.0, 1.0)) for c in cols}
        snap = mon.record(row)
    # 마지막 snapshot
    assert snap.sample_size == 30
    # 학습 분포에 가까운 입력 → outlier rate 가 ~5% (학습 임계 p95) 근처
    assert snap.outlier_rate <= 0.40
    assert snap.is_alert is False or snap.outlier_rate <= mon.alert_threshold


def test_monitor_alert_when_distribution_shifts():
    ae, cols = _train_tiny_ae()
    mon = DriftMonitor({"ae": ae}, window_size=40, alert_threshold=0.20, min_samples_for_alert=20)
    # OOD inputs (far from train distribution)
    rng = np.random.default_rng(11)
    for _ in range(40):
        row = {c: float(rng.normal(10.0, 2.0)) for c in cols}   # mean 10 vs train 0
        snap = mon.record(row)
    # 분포 시프트 → outlier rate 가 임계 초과
    assert snap.sample_size == 40
    assert snap.outlier_rate > 0.5, f"expected high outlier rate, got {snap.outlier_rate}"
    assert snap.is_alert is True


def test_monitor_window_caps_at_window_size():
    ae, cols = _train_tiny_ae()
    mon = DriftMonitor({"ae": ae}, window_size=10)
    for i in range(50):
        mon.record({c: 0.0 for c in cols})
    state = mon.state()
    assert state["current_sample_count"] == 10


def test_monitor_record_does_not_raise_on_garbage_input():
    """가비지 입력에도 raise 안 함 — main loop 안전성."""
    ae, cols = _train_tiny_ae()
    mon = DriftMonitor({"ae": ae})
    # missing keys, None, weird types
    snap1 = mon.record({})
    snap2 = mon.record({c: None for c in cols})
    snap3 = mon.record({"f0": "not_a_number"})
    snap4 = mon.record({c: float("inf") for c in cols})
    snap5 = mon.record({c: float("nan") for c in cols})
    for snap in (snap1, snap2, snap3, snap4, snap5):
        assert isinstance(snap, DriftSnapshot)


def test_monitor_below_min_samples_does_not_alert():
    """표본 부족 시 alert 안 함 — false alarm 방지."""
    ae, cols = _train_tiny_ae()
    mon = DriftMonitor({"ae": ae}, window_size=50, alert_threshold=0.10, min_samples_for_alert=30)
    rng = np.random.default_rng(13)
    # 학습 분포 밖 입력만 10개 → outlier rate 100% 일 수 있지만 표본 부족 → no alert
    for _ in range(10):
        snap = mon.record({c: float(rng.normal(10.0, 1.0)) for c in cols})
    assert snap.is_alert is False
    assert snap.sample_size == 10


def test_monitor_reset_clears_state():
    ae, cols = _train_tiny_ae()
    mon = DriftMonitor({"ae": ae})
    for _ in range(20):
        mon.record({c: 0.0 for c in cols})
    assert mon.state()["current_sample_count"] == 20
    mon.reset()
    assert mon.state()["current_sample_count"] == 0
