"""
core/daytrade_dl_lstm.py — LSTM (PyTorch, CPU) for 60-min sequence-based TP prediction.

매니페스트 phase_3 rank 2:
  - 60-min OHLCV+TA window 입력
  - Krauss-Do-Huck 2017 EJOR / Sirignano-Cont 2019 QF 참조
  - PyTorch CPU only (sandbox 환경)
  - paper-only — main loop 진입 결정 영향 0
  - artifact path: `models/research/dl_lstm_*.pt` + `models/research/dl_lstm_*_meta.joblib`

설계:
  - 입력 shape: (batch, seq_len=60, feature_dim)
  - LSTM 2-layer hidden=64 → dropout → linear → sigmoid (binary TP probability)
  - 분포 시프트 견고성 위해 LayerNorm + 시간 가중 + early stopping
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np


@dataclass
class LSTMConfig:
    seq_len: int = 60
    feature_dim: int = 7        # OHLCV + ma120 + atr_pct 등 (sequence input)
    hidden_size: int = 64
    num_layers: int = 2
    dropout: float = 0.3
    bidirectional: bool = False
    learning_rate: float = 1e-3
    weight_decay: float = 1e-5
    batch_size: int = 64
    max_epochs: int = 30
    patience: int = 5           # early stopping
    random_state: int = 42
    device: str = "cpu"


def _safe_torch():
    """torch 가져오기 — import 실패 시 None 반환. main loop 안전."""
    try:
        import torch
        return torch
    except Exception:
        return None


class LSTMBinaryClassifier:
    """LSTM-based binary classifier (TP / no-TP).

    paper-only research model. main loop / order code 와 절대 결합 X.
    """

    def __init__(self, cfg: LSTMConfig):
        self.cfg = cfg
        self._torch = _safe_torch()
        self._model = None
        self._scaler_mean: np.ndarray | None = None
        self._scaler_std:  np.ndarray | None = None

    def _build_module(self):
        torch = self._torch
        nn = torch.nn

        class _LSTMNet(nn.Module):
            def __init__(self, feat_dim, hidden, n_layers, dropout, bidir):
                super().__init__()
                self.ln_in = nn.LayerNorm(feat_dim)
                self.lstm = nn.LSTM(
                    input_size=feat_dim,
                    hidden_size=hidden,
                    num_layers=n_layers,
                    dropout=(dropout if n_layers > 1 else 0.0),
                    bidirectional=bidir,
                    batch_first=True,
                )
                out_dim = hidden * (2 if bidir else 1)
                self.dropout = nn.Dropout(dropout)
                self.fc = nn.Linear(out_dim, 2)   # 2-class output (logits)

            def forward(self, x):
                x = self.ln_in(x)
                out, _ = self.lstm(x)
                last = out[:, -1, :]    # 마지막 timestep
                last = self.dropout(last)
                return self.fc(last)

        return _LSTMNet(
            self.cfg.feature_dim, self.cfg.hidden_size, self.cfg.num_layers,
            self.cfg.dropout, self.cfg.bidirectional,
        )

    def fit(
        self,
        X: np.ndarray,       # (n, seq_len, feature_dim)
        y: np.ndarray,       # (n,) ∈ {0, 1}
        sample_weight: np.ndarray | None = None,
        val_split: float = 0.15,
        verbose: bool = True,
    ) -> dict:
        """학습 + early stopping. fit history 반환."""
        torch = self._torch
        if torch is None:
            raise RuntimeError("PyTorch not available")

        torch.manual_seed(self.cfg.random_state)
        np.random.seed(self.cfg.random_state)

        # 시간 순서 보존 split (랜덤 X — leakage 방지)
        n = len(X)
        n_val = max(1, int(n * val_split))
        n_train = n - n_val
        X_tr, X_va = X[:n_train], X[n_train:]
        y_tr, y_va = y[:n_train], y[n_train:]
        if sample_weight is not None:
            w_tr = sample_weight[:n_train]
        else:
            w_tr = np.ones(n_train, dtype=float)

        # feature-wise standardization (train 분포 기준)
        flat = X_tr.reshape(-1, X_tr.shape[-1])
        self._scaler_mean = flat.mean(axis=0)
        self._scaler_std  = flat.std(axis=0) + 1e-8
        X_tr_s = (X_tr - self._scaler_mean) / self._scaler_std
        X_va_s = (X_va - self._scaler_mean) / self._scaler_std

        device = torch.device(self.cfg.device)
        self._model = self._build_module().to(device)

        criterion = torch.nn.CrossEntropyLoss(reduction="none")
        optimizer = torch.optim.Adam(
            self._model.parameters(), lr=self.cfg.learning_rate,
            weight_decay=self.cfg.weight_decay,
        )

        best_val_loss = float("inf")
        best_state = None
        patience = self.cfg.patience
        history = {"train_loss": [], "val_loss": [], "val_auc": []}

        from sklearn.metrics import roc_auc_score

        def _iter_batches(X_arr, y_arr, w_arr=None, bs=self.cfg.batch_size, shuffle=False):
            idx = np.arange(len(X_arr))
            if shuffle:
                rng = np.random.default_rng(self.cfg.random_state)
                rng.shuffle(idx)
            for i in range(0, len(idx), bs):
                sl = idx[i:i + bs]
                xb = torch.tensor(X_arr[sl], dtype=torch.float32, device=device)
                yb = torch.tensor(y_arr[sl], dtype=torch.long, device=device)
                wb = torch.tensor(w_arr[sl] if w_arr is not None else np.ones(len(sl)),
                                  dtype=torch.float32, device=device)
                yield xb, yb, wb

        for epoch in range(self.cfg.max_epochs):
            self._model.train()
            running = 0.0
            count = 0
            for xb, yb, wb in _iter_batches(X_tr_s, y_tr, w_tr, shuffle=True):
                optimizer.zero_grad()
                logits = self._model(xb)
                loss_per = criterion(logits, yb)
                loss = (loss_per * wb).sum() / wb.sum().clamp(min=1.0)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(self._model.parameters(), 1.0)
                optimizer.step()
                running += float(loss.detach().cpu()) * len(xb)
                count += len(xb)
            train_loss = running / max(1, count)

            # validation
            self._model.eval()
            val_loss = 0.0
            val_count = 0
            val_probs = []
            with torch.no_grad():
                for xb, yb, _ in _iter_batches(X_va_s, y_va):
                    logits = self._model(xb)
                    probs = torch.softmax(logits, dim=1)[:, 1].cpu().numpy()
                    val_probs.extend(probs.tolist())
                    val_loss += float(criterion(logits, yb).mean().cpu()) * len(xb)
                    val_count += len(xb)
            val_loss /= max(1, val_count)
            try:
                val_auc = float(roc_auc_score(y_va, val_probs)) if len(set(y_va)) > 1 else 0.5
            except Exception:
                val_auc = 0.5

            history["train_loss"].append(train_loss)
            history["val_loss"].append(val_loss)
            history["val_auc"].append(val_auc)

            if verbose:
                print(f"  epoch {epoch+1:2d}/{self.cfg.max_epochs} | train_loss={train_loss:.4f} | val_loss={val_loss:.4f} | val_auc={val_auc:.4f}")

            if val_loss < best_val_loss - 1e-4:
                best_val_loss = val_loss
                best_state = {k: v.detach().cpu().clone() for k, v in self._model.state_dict().items()}
                patience = self.cfg.patience
            else:
                patience -= 1
                if patience <= 0:
                    if verbose:
                        print(f"  early stopping at epoch {epoch+1}")
                    break

        if best_state is not None:
            self._model.load_state_dict(best_state)
        return history

    def predict_proba(self, X: np.ndarray) -> np.ndarray:
        """returns (n, 2) probabilities."""
        torch = self._torch
        if torch is None or self._model is None:
            return np.full((len(X), 2), 0.5, dtype=float)
        device = torch.device(self.cfg.device)
        X_s = (X - self._scaler_mean) / self._scaler_std
        self._model.eval()
        probs = []
        with torch.no_grad():
            for i in range(0, len(X_s), self.cfg.batch_size):
                xb = torch.tensor(X_s[i:i + self.cfg.batch_size], dtype=torch.float32, device=device)
                logits = self._model(xb)
                p = torch.softmax(logits, dim=1).cpu().numpy()
                probs.append(p)
        return np.concatenate(probs, axis=0) if probs else np.zeros((0, 2), dtype=float)

    def state_for_save(self) -> dict:
        """artifact 저장용 dict — joblib 호환."""
        torch = self._torch
        if torch is None or self._model is None:
            return {}
        return {
            "state_dict": {k: v.cpu().numpy() for k, v in self._model.state_dict().items()},
            "scaler_mean": self._scaler_mean,
            "scaler_std":  self._scaler_std,
            "cfg": self.cfg,
        }

    def load_from_state(self, state: dict) -> None:
        torch = self._torch
        if torch is None:
            raise RuntimeError("PyTorch not available")
        self.cfg = state.get("cfg", self.cfg)
        self._scaler_mean = state["scaler_mean"]
        self._scaler_std  = state["scaler_std"]
        self._model = self._build_module()
        sd = {k: torch.from_numpy(np.asarray(v)) for k, v in state["state_dict"].items()}
        self._model.load_state_dict(sd)
        self._model.eval()


def build_sequence_features(
    minute_bars: list[dict],
    *,
    seq_len: int = 60,
    feature_keys: tuple[str, ...] = (
        "close", "high", "low", "volume", "ma120", "atr_pct", "rsi",
    ),
) -> np.ndarray:
    """OHLCV 분봉 시퀀스 → (seq_len, len(feature_keys)) numpy 배열.

    분봉이 seq_len 보다 적으면 zero padding (앞쪽).
    OHLC 는 첫 close 기준 로그 수익률로 정규화 (시퀀스 안정성).
    Volume / ma120 / atr_pct / rsi 는 raw 사용 (학습 시 standardize).
    """
    n = len(minute_bars)
    out = np.zeros((seq_len, len(feature_keys)), dtype=float)
    if n == 0:
        return out

    start_idx = max(0, n - seq_len)
    bars = minute_bars[start_idx:]
    base_close = float(bars[0].get("close", 1.0) or 1.0)
    if base_close <= 0:
        base_close = 1.0
    pad = seq_len - len(bars)
    for i, b in enumerate(bars):
        for j, key in enumerate(feature_keys):
            v = b.get(key, 0.0)
            try:
                f = float(v) if v is not None else 0.0
            except (TypeError, ValueError):
                f = 0.0
            # OHLC: log-return vs base
            if key in ("close", "high", "low", "open"):
                if f > 0 and base_close > 0:
                    f = float(np.log(f / base_close))
                else:
                    f = 0.0
            out[pad + i, j] = f
    return out
