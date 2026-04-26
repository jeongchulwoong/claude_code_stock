"""
core/fundamental_gate.py — 매수 직전 펀더멘탈 4중 필터

작전주·적자기업·거품주를 자동 제외해서 단타 진입 전에 한 번 거른다.
yfinance 1회 호출로 4개 지표를 가져와서 평가한다 (6시간 캐시).

조건 (전부 AND):
  1. 영업이익률 > 0      → 흑자 기업
  2. 부채비율  < 200%   → 자본 대비 부채 2배 이내
  3. ROE       > 5%     → 자본 효율성 최소선
  4. PER  0 < x < 50    → 적자(<0)도 거품(>50)도 컷
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from loguru import logger

from config import RISK_CONFIG


@dataclass
class FundamentalCheck:
    passed:  bool
    reasons: list[str] = field(default_factory=list)   # 각 조건 통과/실패 사유 (사람용)
    raw:     dict      = field(default_factory=dict)   # 원본 지표 (op_margin, debt_eq, roe, per)


class FundamentalGate:
    """6시간 in-memory 캐시 + yfinance 단일 호출."""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, FundamentalCheck]] = {}
        cfg = RISK_CONFIG
        self._enabled        = cfg.get("fundamental_gate_enabled", True)
        self._min_op_margin  = cfg.get("fund_min_op_margin", 0.0)
        self._max_debt_eq    = cfg.get("fund_max_debt_to_equity", 200.0)
        self._min_roe        = cfg.get("fund_min_roe", 0.05)
        self._per_min        = cfg.get("fund_per_min", 0.0)
        self._per_max        = cfg.get("fund_per_max", 50.0)
        self._allow_missing  = cfg.get("fund_allow_missing_data", False)
        self._cache_ttl      = cfg.get("fund_cache_hours", 6) * 3600
        logger.info(
            "FundamentalGate {} | 영업익>{:.0%}, 부채<{:.0f}%, ROE>{:.0%}, PER {:.0f}~{:.0f}",
            "활성" if self._enabled else "비활성",
            self._min_op_margin, self._max_debt_eq, self._min_roe,
            self._per_min, self._per_max,
        )

    def check(self, ticker: str) -> FundamentalCheck:
        if not self._enabled:
            return FundamentalCheck(passed=True, reasons=["게이트 비활성"], raw={})

        now = time.time()
        cached = self._cache.get(ticker)
        if cached and now - cached[0] < self._cache_ttl:
            return cached[1]

        result = self._fetch_and_evaluate(ticker)
        self._cache[ticker] = (now, result)
        return result

    def clear_cache(self, ticker: str | None = None) -> None:
        """수동 리셋. ticker=None 이면 전체."""
        if ticker:
            self._cache.pop(ticker, None)
        else:
            self._cache.clear()

    # ── 내부 ──────────────────────────────

    def _fetch_and_evaluate(self, ticker: str) -> FundamentalCheck:
        try:
            import yfinance as yf
            info = yf.Ticker(ticker).info
            if not isinstance(info, dict) or not info:
                return self._missing_data(ticker, "yfinance.info 빈 응답")
        except Exception as e:
            return self._missing_data(ticker, f"yfinance 오류: {type(e).__name__}")

        op_margin = info.get("operatingMargins")  # 영업이익률 (decimal, e.g., 0.15)
        debt_eq   = info.get("debtToEquity")      # 부채비율 (% — 156.7 = 156.7%)
        roe       = info.get("returnOnEquity")    # ROE (decimal)
        # PER: trailingPE → forwardPE → priceToEarnings 순으로 폴백
        # (yfinance가 한국주식엔 trailingPE=None/0 주는 경우 많음)
        per_raw = (info.get("trailingPE")
                   or info.get("forwardPE")
                   or info.get("priceToEarnings"))

        # 핵심 4개 중 None 너무 많으면 데이터 부족 처리
        missing = sum(1 for v in (op_margin, debt_eq, roe, per_raw) if v is None)
        if missing >= 3:
            return self._missing_data(ticker, f"핵심 지표 {missing}/4 누락")

        op_margin = float(op_margin or 0)
        debt_eq   = float(debt_eq or 0)
        roe       = float(roe or 0)
        per       = float(per_raw or 0)
        per_available = per_raw is not None and per > 0

        reasons: list[str] = []
        passed = True

        # 1) 영업이익률
        if op_margin <= self._min_op_margin:
            reasons.append(f"❌ 영업익 {op_margin:+.1%}")
            passed = False
        else:
            reasons.append(f"✅ 영업익 {op_margin:+.1%}")

        # 2) 부채비율 (없으면 0으로 통과 — 일부 신생/현금부자 기업 보호)
        if debt_eq > self._max_debt_eq:
            reasons.append(f"❌ 부채비율 {debt_eq:.0f}%")
            passed = False
        else:
            reasons.append(f"✅ 부채비율 {debt_eq:.0f}%")

        # 3) ROE
        if roe < self._min_roe:
            reasons.append(f"❌ ROE {roe:+.1%}")
            passed = False
        else:
            reasons.append(f"✅ ROE {roe:+.1%}")

        # 4) PER — 데이터 있을 때만 검사 (yfinance가 한국주식 PER 종종 누락)
        if not per_available:
            reasons.append(f"⚪ PER 데이터 없음 (스킵)")
        elif not (self._per_min < per < self._per_max):
            reasons.append(f"❌ PER {per:.1f}")
            passed = False
        else:
            reasons.append(f"✅ PER {per:.1f}")

        return FundamentalCheck(
            passed=passed,
            reasons=reasons,
            raw={"op_margin": op_margin, "debt_eq": debt_eq, "roe": roe, "per": per,
                 "name": info.get("shortName", "")},
        )

    def _missing_data(self, ticker: str, why: str) -> FundamentalCheck:
        passed = self._allow_missing
        return FundamentalCheck(
            passed=passed,
            reasons=[f"⚠️ 데이터 부족 — {why} → {'통과' if passed else '차단'}"],
            raw={},
        )
