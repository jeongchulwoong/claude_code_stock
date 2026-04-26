"""
Adaptive tuning for live trading risk parameters.

The tuner uses closed AI signal outcomes from ai_signals and updates
user_config.json with conservative bounds. It optimizes for net expectancy
after round-trip costs and a slippage buffer, not raw win rate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from statistics import mean

from loguru import logger

from config import DB_PATH, RISK_CONFIG, USER_CONFIG_PATH


@dataclass
class TuningResult:
    changed: bool
    trades: int
    winrate: float
    avg_net_pnl_pct: float
    profit_factor: float
    min_confidence: int
    min_effective_rr: float
    note: str


class AdaptiveTuner:
    """Daily closed-trade feedback loop for confidence, ATR and R:R gates."""

    MIN_TRADES = 12
    MIN_BUCKET_TRADES = 5
    LOOKBACK_DAYS = 90

    def __init__(
        self,
        db_path: Path = DB_PATH,
        user_config_path: Path = USER_CONFIG_PATH,
    ) -> None:
        self._db_path = db_path
        self._cfg_path = user_config_path

    def tune(self, force: bool = False) -> TuningResult:
        cfg = self._load_config()
        today = date.today().isoformat()
        meta = cfg.get("adaptive_tuning", {})
        if not force and meta.get("last_tuned_date") == today:
            return self._result_from_config(cfg, "already tuned today")

        risk_cfg = dict(cfg.get("risk_config", {}))
        trades = self._load_closed_trades(risk_cfg)
        stats = self._stats(trades)

        before = json.dumps(cfg.get("risk_config", {}), sort_keys=True, ensure_ascii=False)
        self._apply_recommendations(cfg, risk_cfg, trades, stats)
        self._retune_priority_watchlist(cfg, trades)

        cfg["adaptive_tuning"] = {
            "last_tuned_date": today,
            "lookback_days": self.LOOKBACK_DAYS,
            "sample_trades": stats["n"],
            "winrate": round(stats["winrate"], 4),
            "avg_net_pnl_pct": round(stats["avg_net"], 4),
            "profit_factor": round(stats["profit_factor"], 4),
            "updated_at": datetime.now().isoformat(timespec="seconds"),
        }

        after = json.dumps(cfg.get("risk_config", {}), sort_keys=True, ensure_ascii=False)
        changed = before != after
        if changed or force:
            self._save_config(cfg)

        self._apply_runtime_config(cfg)
        result = self._result_from_config(
            cfg,
            "insufficient sample, safety floors applied" if stats["n"] < self.MIN_TRADES else "tuned from closed trades",
        )
        result.changed = changed
        result.trades = stats["n"]
        result.winrate = stats["winrate"]
        result.avg_net_pnl_pct = stats["avg_net"]
        result.profit_factor = stats["profit_factor"]
        logger.info(
            "Adaptive tuning | changed={} trades={} wr={:.1%} avg_net={:+.2f}% pf={:.2f} min_conf={} rr={:.2f}",
            changed, result.trades, result.winrate, result.avg_net_pnl_pct,
            result.profit_factor, result.min_confidence, result.min_effective_rr,
        )
        return result

    def _load_closed_trades(self, risk_cfg: dict) -> list[dict]:
        cutoff = (date.today() - timedelta(days=self.LOOKBACK_DAYS)).isoformat()
        cost_pct = float(risk_cfg.get("cost_roundtrip_pct", RISK_CONFIG.get("cost_roundtrip_pct", 0.004))) * 100
        slippage_pct = float(risk_cfg.get("slippage_roundtrip_pct", 0.003)) * 100
        rows: list[dict] = []
        if not self._db_path.exists():
            return rows

        with sqlite3.connect(self._db_path) as con:
            con.row_factory = sqlite3.Row
            try:
                data = con.execute(
                    """
                    SELECT ticker, name, entry_at, ai_confidence, setup_type,
                           composite, tech_score, pnl_pct, won, holding_min
                    FROM ai_signals
                    WHERE ai_action='BUY'
                      AND exit_at IS NOT NULL
                      AND pnl_pct IS NOT NULL
                      AND DATE(entry_at) >= ?
                    ORDER BY entry_at DESC
                    """,
                    (cutoff,),
                ).fetchall()
            except sqlite3.OperationalError:
                return rows

        for r in data:
            pnl = float(r["pnl_pct"] or 0.0)
            rows.append({
                "ticker": r["ticker"],
                "name": r["name"] or r["ticker"],
                "confidence": float(r["ai_confidence"] or 0.0),
                "setup_type": r["setup_type"] or "",
                "pnl_pct": pnl,
                "net_pnl_pct": pnl - cost_pct - slippage_pct,
                "won": bool(r["won"]),
            })
        return rows

    @staticmethod
    def _stats(trades: list[dict]) -> dict:
        n = len(trades)
        if not n:
            return {"n": 0, "winrate": 0.0, "avg_net": 0.0, "profit_factor": 0.0}
        net = [float(t["net_pnl_pct"]) for t in trades]
        wins = [x for x in net if x > 0]
        losses = [x for x in net if x <= 0]
        gross_win = sum(wins)
        gross_loss = abs(sum(losses))
        return {
            "n": n,
            "winrate": len(wins) / n,
            "avg_net": mean(net),
            "profit_factor": gross_win / gross_loss if gross_loss > 0 else (9.99 if gross_win > 0 else 0.0),
        }

    def _apply_recommendations(self, cfg: dict, risk_cfg: dict, trades: list[dict], stats: dict) -> None:
        current_conf = int(risk_cfg.get("min_confidence", RISK_CONFIG.get("min_confidence", 75)))
        current_rr = float(risk_cfg.get("min_effective_rr", RISK_CONFIG.get("min_effective_rr", 1.8)))
        current_sl = float(risk_cfg.get("stop_loss_atr_mult", RISK_CONFIG.get("stop_loss_atr_mult", 1.5)))
        current_tp = float(risk_cfg.get("take_profit_atr_mult", RISK_CONFIG.get("take_profit_atr_mult", 3.0)))

        if stats["n"] < self.MIN_TRADES:
            new_conf = max(70, current_conf)
            new_rr = max(1.8, current_rr)
            new_sl = current_sl
            new_tp = current_tp
            max_positions = min(2, int(risk_cfg.get("max_positions", RISK_CONFIG.get("max_positions", 2))))
        else:
            new_conf = self._select_confidence_threshold(trades, current_conf)
            new_rr = current_rr
            new_sl = current_sl
            new_tp = current_tp
            max_positions = int(risk_cfg.get("max_positions", RISK_CONFIG.get("max_positions", 2)))

            if stats["avg_net"] < 0 or stats["profit_factor"] < 1.05:
                new_conf = max(new_conf, min(90, current_conf + 5))
                new_rr = min(2.6, current_rr + 0.2)
                new_sl = max(1.2, current_sl - 0.1)
                new_tp = min(4.0, current_tp + 0.2)
                max_positions = 1
            elif stats["avg_net"] > 0.35 and stats["winrate"] >= 0.55 and stats["profit_factor"] >= 1.3:
                new_conf = max(70, min(new_conf, current_conf))
                new_rr = max(1.8, current_rr - 0.1)
                max_positions = min(2, max_positions)

        risk_cfg.update({
            "min_confidence": self._clamp_int(new_conf, 65, 90),
            "min_effective_rr": round(self._clamp_float(new_rr, 1.6, 2.8), 2),
            "stop_loss_atr_mult": round(self._clamp_float(new_sl, 1.0, 2.5), 2),
            "take_profit_atr_mult": round(self._clamp_float(new_tp, 2.0, 4.5), 2),
            "max_positions": self._clamp_int(max_positions, 1, 3),
            "slippage_roundtrip_pct": round(float(risk_cfg.get("slippage_roundtrip_pct", 0.003)), 4),
        })
        cfg["risk_config"] = risk_cfg

    def _select_confidence_threshold(self, trades: list[dict], current: int) -> int:
        qualified: list[int] = []
        for threshold in (65, 70, 75, 80, 85, 90):
            subset = [t for t in trades if t["confidence"] >= threshold]
            if len(subset) < self.MIN_BUCKET_TRADES:
                continue
            s = self._stats(subset)
            if s["avg_net"] > 0.15 and s["winrate"] >= 0.50 and s["profit_factor"] >= 1.15:
                qualified.append(threshold)
        if qualified:
            return max(70, min(qualified))
        return max(80, min(90, current + 5))

    def _retune_priority_watchlist(self, cfg: dict, trades: list[dict]) -> None:
        if not trades:
            return
        try:
            from stock_universe import get_name
        except Exception:
            get_name = lambda ticker: ticker

        by_ticker: dict[str, list[float]] = {}
        for t in trades:
            by_ticker.setdefault(t["ticker"], []).append(float(t["net_pnl_pct"]))

        winners = []
        blocked = set()
        for ticker, pnls in by_ticker.items():
            n = len(pnls)
            avg = mean(pnls)
            wr = sum(1 for p in pnls if p > 0) / n
            if n >= 2 and avg > 0.1 and wr >= 0.5:
                winners.append((avg * min(n, 5), get_name(ticker)))
            if n >= 3 and avg < -0.3 and wr < 0.4:
                blocked.add(get_name(ticker))

        if not winners and not blocked:
            return

        current = list(cfg.get("priority_watch_names") or cfg.get("watch_names") or [])
        if not current:
            try:
                from config import WATCH_LIST
                current = list(WATCH_LIST)
            except Exception:
                current = []
        winner_names = [name for _, name in sorted(winners, reverse=True)]
        merged = []
        for name in winner_names + current:
            if name in blocked or name in merged:
                continue
            merged.append(name)
            if len(merged) >= 30:
                break
        if merged:
            cfg["priority_watch_names"] = merged

    def _result_from_config(self, cfg: dict, note: str) -> TuningResult:
        risk = cfg.get("risk_config", {})
        meta = cfg.get("adaptive_tuning", {})
        return TuningResult(
            changed=False,
            trades=int(meta.get("sample_trades", 0) or 0),
            winrate=float(meta.get("winrate", 0.0) or 0.0),
            avg_net_pnl_pct=float(meta.get("avg_net_pnl_pct", 0.0) or 0.0),
            profit_factor=float(meta.get("profit_factor", 0.0) or 0.0),
            min_confidence=int(risk.get("min_confidence", RISK_CONFIG.get("min_confidence", 75))),
            min_effective_rr=float(risk.get("min_effective_rr", RISK_CONFIG.get("min_effective_rr", 1.8))),
            note=note,
        )

    def _apply_runtime_config(self, cfg: dict) -> None:
        risk = cfg.get("risk_config", {})
        RISK_CONFIG.update(risk)
        RISK_CONFIG["min_confidence"] = self._clamp_int(RISK_CONFIG.get("min_confidence", 75), 65, 90)
        RISK_CONFIG["min_effective_rr"] = self._clamp_float(RISK_CONFIG.get("min_effective_rr", 1.8), 1.6, 2.8)

    def _load_config(self) -> dict:
        if self._cfg_path.exists():
            try:
                return json.loads(self._cfg_path.read_text(encoding="utf-8"))
            except Exception as e:
                logger.warning("user_config read failed: {}", e)
        return {}

    def _save_config(self, cfg: dict) -> None:
        self._cfg_path.write_text(
            json.dumps(cfg, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    @staticmethod
    def _clamp_int(value, low: int, high: int) -> int:
        return max(low, min(high, int(value)))

    @staticmethod
    def _clamp_float(value, low: float, high: float) -> float:
        return max(low, min(high, float(value)))
