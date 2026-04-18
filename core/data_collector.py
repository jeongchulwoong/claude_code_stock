"""
core/data_collector.py — 시세·차트·거래량 수집 모듈

키움 TR 목록:
  OPT10001 — 주식기본정보 (현재가, PER, 외인 보유율 등)
  OPT10081 — 주식일봉차트
  OPT10080 — 주식분봉차트
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd
from loguru import logger

from config import LOG_DIR, SCHEDULE_CONFIG
from core.kiwoom_api import KiwoomNotConnectedError


# ── 데이터 구조 ──────────────────────────────

@dataclass
class StockSnapshot:
    """AI 판단 엔진에 전달할 종목 스냅샷"""
    ticker: str
    name: str
    current_price: int
    open_price: int
    high_price: int
    low_price: int
    volume: int
    volume_ratio: float          # 평균 대비 거래량 비율
    per: float
    foreigner_pct: float         # 외국인 보유 비율 (%)
    # 기술지표 (DataCollector가 계산)
    rsi: float = 0.0
    macd: float = 0.0
    macd_signal: float = 0.0
    macd_cross: bool = False     # 골든크로스 발생 여부
    bollinger_upper: float = 0.0
    bollinger_lower: float = 0.0
    bollinger_position: str = "middle"   # "upper" | "lower" | "middle"
    ma5: float = 0.0
    ma20: float = 0.0
    ma5_cross_ma20: bool = False
    stochastic_k: float = 0.0
    # 원시 일봉 데이터 (백테스팅·검증용)
    daily_df: pd.DataFrame = field(default_factory=pd.DataFrame)


# ── DataCollector ─────────────────────────────

class DataCollector:
    """
    키움 API를 통해 종목 데이터를 수집하고
    기술지표를 계산하여 StockSnapshot을 반환한다.
    """

    # 스크린 번호 (키움 API 요구사항 — 4자리 문자열)
    _SCR_BASIC   = "1000"
    _SCR_DAILY   = "1001"
    _SCR_MINUTE  = "1002"

    # TR 요청 간격 (키움 API는 초당 5회 제한)
    _TR_DELAY_SEC = 0.22

    def __init__(self, kiwoom) -> None:
        """
        kiwoom: KiwoomAPI | MockKiwoomAPI 인스턴스
        """
        self._kw = kiwoom
        self._tr_result: dict = {}
        self._tr_done = False

    # ── 퍼블릭 API ────────────────────────────

    def get_snapshot(self, ticker: str) -> StockSnapshot:
        """
        종목코드를 받아 StockSnapshot을 반환한다.
        내부적으로 기본정보 TR + 일봉차트 TR을 순차 요청한다.
        """
        logger.info("스냅샷 수집 시작: {}", ticker)

        basic   = self._fetch_basic_info(ticker)
        daily_df = self._fetch_daily_chart(ticker, count=60)

        if daily_df.empty:
            logger.warning("일봉 데이터 없음 — 기술지표 계산 불가: {}", ticker)
            indicators = {}
        else:
            indicators = self._calc_indicators(daily_df)

        snap = StockSnapshot(
            ticker        = ticker,
            name          = basic.get("name", ""),
            current_price = int(basic.get("current_price", 0)),
            open_price    = int(basic.get("open_price", 0)),
            high_price    = int(basic.get("high_price", 0)),
            low_price     = int(basic.get("low_price", 0)),
            volume        = int(basic.get("volume", 0)),
            volume_ratio  = float(basic.get("volume_ratio", 1.0)),
            per           = float(basic.get("per", 0.0)),
            foreigner_pct = float(basic.get("foreigner_pct", 0.0)),
            daily_df      = daily_df,
            **indicators,
        )

        logger.info(
            "스냅샷 완료: {} | 현재가={:,} | RSI={:.1f}",
            ticker, snap.current_price, snap.rsi,
        )
        return snap

    def get_snapshots(self, tickers: list[str]) -> list[StockSnapshot]:
        """여러 종목 스냅샷을 순차 수집 (TR 딜레이 적용)"""
        results = []
        for ticker in tickers:
            try:
                results.append(self.get_snapshot(ticker))
            except Exception as e:
                logger.error("스냅샷 수집 실패: {} | {}", ticker, e)
            time.sleep(self._TR_DELAY_SEC * 2)
        return results

    # ── TR 요청 (내부) ────────────────────────

    def _fetch_basic_info(self, ticker: str) -> dict:
        """OPT10001 — 주식기본정보 조회"""
        self._kw.set_input_value("종목코드", ticker)
        self._tr_done = False
        self._tr_result = {}

        self._kw.comm_rq_data(
            rq_name  = "주식기본정보",
            tr_code  = "OPT10001",
            prev_next = 0,
            scr_no   = self._SCR_BASIC,
            callback = self._on_basic_info,
        )
        self._wait_tr()

        return self._tr_result

    def _fetch_daily_chart(self, ticker: str, count: int = 60) -> pd.DataFrame:
        """OPT10081 — 주식일봉차트 조회 (최근 count일)"""
        self._kw.set_input_value("종목코드", ticker)
        self._kw.set_input_value("기준일자", "")
        self._kw.set_input_value("수정주가구분", "1")
        self._tr_done = False
        self._tr_result = {}

        self._kw.comm_rq_data(
            rq_name  = "주식일봉차트",
            tr_code  = "OPT10081",
            prev_next = 0,
            scr_no   = self._SCR_DAILY,
            callback = self._on_daily_chart,
        )
        self._wait_tr()

        return self._tr_result.get("df", pd.DataFrame())

    # ── TR 콜백 ──────────────────────────────

    def _on_basic_info(self, scr_no, rq_name, tr_code, prev_next) -> None:
        """OPT10001 수신 처리"""
        def g(item):
            return self._kw.get_comm_data(tr_code, rq_name, 0, item)

        self._tr_result = {
            "name":          g("종목명"),
            "current_price": abs(int(g("현재가") or 0)),
            "open_price":    abs(int(g("시가") or 0)),
            "high_price":    abs(int(g("고가") or 0)),
            "low_price":     abs(int(g("저가") or 0)),
            "volume":        abs(int(g("거래량") or 0)),
            "volume_ratio":  float(g("거래량대비") or 1.0),
            "per":           float(g("PER") or 0.0),
            "foreigner_pct": float(g("외인소진율") or 0.0),
        }
        self._tr_done = True

    def _on_daily_chart(self, scr_no, rq_name, tr_code, prev_next) -> None:
        """OPT10081 수신 처리 → DataFrame 변환"""
        rows = []
        i = 0
        while True:
            date = self._kw.get_comm_data(tr_code, rq_name, i, "일자")
            if not date:
                break
            rows.append({
                "date":   date.strip(),
                "open":   abs(int(self._kw.get_comm_data(tr_code, rq_name, i, "시가") or 0)),
                "high":   abs(int(self._kw.get_comm_data(tr_code, rq_name, i, "고가") or 0)),
                "low":    abs(int(self._kw.get_comm_data(tr_code, rq_name, i, "저가") or 0)),
                "close":  abs(int(self._kw.get_comm_data(tr_code, rq_name, i, "현재가") or 0)),
                "volume": abs(int(self._kw.get_comm_data(tr_code, rq_name, i, "거래량") or 0)),
            })
            i += 1

        if rows:
            df = pd.DataFrame(rows)
            df["date"] = pd.to_datetime(df["date"], format="%Y%m%d")
            df = df.sort_values("date").reset_index(drop=True)
            self._tr_result["df"] = df
        else:
            self._tr_result["df"] = pd.DataFrame()

        self._tr_done = True

    # ── TR 완료 대기 ──────────────────────────

    def _wait_tr(self, timeout: float = 10.0) -> None:
        """TR 응답이 올 때까지 블로킹 대기"""
        start = time.time()
        while not self._tr_done:
            if time.time() - start > timeout:
                raise TimeoutError("TR 응답 타임아웃")
            time.sleep(0.05)
        time.sleep(self._TR_DELAY_SEC)  # 키움 API 딜레이 준수

    # ── 기술지표 계산 ─────────────────────────

    @staticmethod
    def _calc_indicators(df: pd.DataFrame) -> dict:
        """
        일봉 DataFrame을 받아 기술지표 딕셔너리를 반환한다.
        ta 라이브러리 없이 순수 pandas/numpy로 계산한다.
        """
        import numpy as np

        close = df["close"].astype(float)
        high  = df["high"].astype(float)
        low   = df["low"].astype(float)

        result: dict = {}

        # ── RSI (14일) ────────────────────────
        delta = close.diff()
        gain  = delta.clip(lower=0)
        loss  = (-delta).clip(lower=0)
        avg_gain = gain.ewm(com=13, min_periods=14).mean()
        avg_loss = loss.ewm(com=13, min_periods=14).mean()
        rs  = avg_gain / avg_loss.replace(0, float("inf"))
        rsi = 100 - (100 / (1 + rs))
        result["rsi"] = round(float(rsi.iloc[-1]), 2)

        # ── MACD (12, 26, 9) ─────────────────
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd_line   = ema12 - ema26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        prev_diff = macd_line.iloc[-2] - signal_line.iloc[-2]
        curr_diff = macd_line.iloc[-1] - signal_line.iloc[-1]
        result["macd"]        = round(float(macd_line.iloc[-1]), 2)
        result["macd_signal"] = round(float(signal_line.iloc[-1]), 2)
        result["macd_cross"]  = bool(prev_diff < 0 and curr_diff >= 0)  # 골든크로스

        # ── 볼린저 밴드 (20일, 2σ) ───────────
        ma20   = close.rolling(20).mean()
        std20  = close.rolling(20).std()
        upper  = ma20 + 2 * std20
        lower  = ma20 - 2 * std20
        price  = float(close.iloc[-1])
        u_val  = float(upper.iloc[-1])
        l_val  = float(lower.iloc[-1])
        if price >= u_val:
            bb_pos = "upper"
        elif price <= l_val:
            bb_pos = "lower"
        else:
            bb_pos = "middle"
        result["bollinger_upper"]    = round(u_val, 0)
        result["bollinger_lower"]    = round(l_val, 0)
        result["bollinger_position"] = bb_pos

        # ── 이동평균 5 / 20 ──────────────────
        ma5_series = close.rolling(5).mean()
        result["ma5"]  = round(float(ma5_series.iloc[-1]), 0)
        result["ma20"] = round(float(ma20.iloc[-1]), 0)
        # 골든크로스: 어제는 ma5 < ma20, 오늘은 ma5 >= ma20
        result["ma5_cross_ma20"] = bool(
            ma5_series.iloc[-2] < ma20.iloc[-2]
            and ma5_series.iloc[-1] >= ma20.iloc[-1]
        )

        # ── 스토캐스틱 (14, 3) ───────────────
        lowest_low   = low.rolling(14).min()
        highest_high = high.rolling(14).max()
        denom = highest_high - lowest_low
        stoch_k = 100 * (close - lowest_low) / denom.replace(0, float("nan"))
        result["stochastic_k"] = round(float(stoch_k.iloc[-1]), 2)

        return result


# ── yfinance 기반 DataCollector ───────────────

class YFinanceDataCollector:
    """
    PyQt5/키움 없이 yfinance + FinanceDataReader로 실제 시세를 수집한다.
    DataCollector와 동일한 get_snapshot / get_snapshots 인터페이스 제공.
    """

    def __init__(self) -> None:
        try:
            import FinanceDataReader as fdr
            self._fdr = fdr
        except ImportError:
            self._fdr = None

    def get_snapshot(self, ticker: str) -> StockSnapshot | None:
        import yfinance as yf
        try:
            # 이름("삼성전자") → 티커("005930.KS") 변환
            from stock_universe import resolve
            ticker, _ = resolve(ticker)

            is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")
            code  = ticker.split(".")[0] if is_kr else None
            df = None

            # 한국 종목: FinanceDataReader 우선
            if is_kr and self._fdr:
                try:
                    raw = self._fdr.DataReader(
                        code,
                        pd.Timestamp.today() - pd.Timedelta(days=200),
                    )
                    if raw is not None and len(raw) >= 20:
                        df = raw.rename(columns=str.lower)
                        df = df.rename(columns={"adj close": "close"})
                except Exception:
                    pass

            if df is None:
                raw = yf.download(ticker, period="6mo", interval="1d",
                                  progress=False, auto_adjust=True)
                if raw is None or len(raw) < 20:
                    return None
                # yfinance ≥0.2 may return MultiIndex columns like ('Close','NKE')
                if isinstance(raw.columns, pd.MultiIndex):
                    raw.columns = [c[0].lower() for c in raw.columns]
                else:
                    raw.columns = [c.lower() for c in raw.columns]
                df = raw

            close_s  = df["close"].squeeze().astype(float)
            volume_s = df["volume"].squeeze().astype(float)

            # 현재가: Google Finance → yfinance fast_info → 종가 순 fallback
            price = None
            try:
                from core.price_fetcher import get_current_price
                gf_price = get_current_price(ticker)
                if gf_price and gf_price > 0:
                    price = int(gf_price)
            except Exception:
                pass
            if not price:
                try:
                    lp = yf.Ticker(ticker).fast_info.last_price
                    if lp and not pd.isna(lp):
                        price = int(float(lp))
                except Exception:
                    pass
            if not price:
                price = int(close_s.iloc[-1])

            vol_avg   = float(volume_s.iloc[-20:-1].mean())
            vol_today = float(volume_s.iloc[-1])
            vol_ratio = round(vol_today / vol_avg, 2) if vol_avg > 0 else 1.0

            # _calc_indicators expects lowercase columns
            indicators = DataCollector._calc_indicators(df)

            from stock_universe import get_name
            name = get_name(ticker)

            return StockSnapshot(
                ticker        = ticker,
                name          = name,
                current_price = price,
                open_price    = int(float(df["open"].iloc[-1])) if "open" in df.columns else price,
                high_price    = int(float(df["high"].iloc[-1])) if "high" in df.columns else price,
                low_price     = int(float(df["low"].iloc[-1]))  if "low"  in df.columns else price,
                volume        = int(vol_today),
                volume_ratio  = vol_ratio,
                per           = 0.0,
                foreigner_pct = 0.0,
                daily_df      = df,
                **indicators,
            )
        except Exception as e:
            logger.error("YFinanceDataCollector 수집 실패: {} | {}", ticker, e)
            return None

    def get_snapshots(self, tickers: list[str]) -> list[StockSnapshot]:
        results = []
        for ticker in tickers:
            snap = self.get_snapshot(ticker)
            if snap:
                results.append(snap)
        return results
