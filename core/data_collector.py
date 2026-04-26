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

import pandas as pd
from loguru import logger


# ── 데이터 구조 ──────────────────────────────

@dataclass
class StockSnapshot:
    """AI 판단 엔진에 전달할 종목 스냅샷"""
    ticker: str
    name: str
    current_price: float    # KRW: 정수, USD/JPY: 소수점 유지
    open_price: float
    high_price: float
    low_price: float
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
    ma120: float = 0.0  # 120일 이동평균 (단타 진입 필터용)
    ma20_slope_pct:      float = 0.0  # MA20 5일 기울기 % (추세 지속/약화 판별)
    uptrend_consistency: float = 0.0  # 최근 20일 close>MA20 비율 % (≥70 = 강한 추세)
    accumulation_ratio:  float = 1.0  # up-day vol / down-day vol (>1.3 = 매집)
    stochastic_k: float = 0.0
    atr: float = 0.0          # 14일 ATR (동적 SL/TP 계산용, 원 단위)
    atr_pct: float = 0.0      # ATR / 현재가 (변동성 백분율)
    # ── 추가 지표 (퀀트 표준) ──────────────────
    mfi: float = 0.0              # Money Flow Index (14일) — 거래대금 가중 RSI
    dist_from_52w_high: float = 0.0   # 52주 신고가 대비 (-N% 떨어진 정도, 음수)
    dist_from_52w_low: float  = 0.0   # 52주 신저가 대비 (+N% 위, 양수)
    value_traded: float = 0.0     # 일평균 거래대금 (원/달러) — 유동성 지표
    obv_trend: float = 0.0        # OBV 20일 변화율 (+ = 매집, - = 분산)
    # ── 고급 메타 지표 (30년차 퀀트 표준) ─────
    bb_width_pct: float = 0.0     # BB 폭 (현재가 대비 %)
    bb_squeeze:   bool  = False   # BB 폭이 60일 평균 대비 60% 미만 = squeeze (변동성 폭발 임박)
    bull_div_rsi: bool  = False   # RSI 불리시 다이버전스 (가격 신저가 + RSI 더 높음)
    bear_div_rsi: bool  = False   # RSI 베어리시 다이버전스 (가격 신고가 + RSI 더 낮음)
    bull_div_obv: bool  = False   # OBV 불리시 (가격 ↓ + OBV ↑)
    bear_div_obv: bool  = False   # OBV 베어리시 (가격 ↑ + OBV ↓)
    # ── 추가 지표 (퀀트 표준 + 한국 시장 특화) ─
    adx:          float = 0.0     # Average Directional Index — 추세 강도 (>25=강한 추세)
    plus_di:      float = 0.0     # +DI (상승 압력)
    minus_di:     float = 0.0     # -DI (하락 압력)
    williams_r:   float = 0.0     # Williams %R (-100~0, -80 미만 = oversold)
    force_index:  float = 0.0     # 가격×거래량 모멘텀 (Elder)
    cmf:          float = 0.0     # Chaikin Money Flow (-1~1, +0.2↑=강한 매집)
    above_cloud:  bool  = False   # Ichimoku 구름 위 (강한 추세)
    below_cloud:  bool  = False   # 구름 아래 (약세)
    cloud_thick:  float = 0.0     # 구름 두께 % (얇으면 추세 전환 임박)
    # ── 한국 시장 마이크로구조 (키움 FID) ──────
    bid_qty:      int   = 0       # 매수 1~5호가 잔량 합
    ask_qty:      int   = 0       # 매도 1~5호가 잔량 합
    bid_ask_ratio: float = 0.0    # bid/ask — >1.5 = 강한 매수세
    foreign_net:  int   = 0       # 외국인 순매수 (당일)
    inst_net:     int   = 0       # 기관 순매수 (당일)
    # ── 인트라데이 멀티타임프레임 (분봉 기반) ──
    rsi_5m:       float = 0.0     # 5분봉 RSI(14) — 진짜 단타 시그널
    rsi_15m:      float = 0.0     # 15분봉 RSI
    intraday_trend: str = ""      # "UP" | "DOWN" | "FLAT" — 5분봉 MA20 기준
    mtf_aligned:  bool  = False   # 1d + 15m + 5m 같은 방향 정렬
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
        kiwoom: KiwoomAPI 또는 KiwoomRestAPI 인스턴스
        """
        self._kw = kiwoom
        self._tr_result: dict = {}
        self._tr_done = False

    # ── 퍼블릭 API ────────────────────────────

    def get_snapshot(self, ticker: str) -> StockSnapshot:
        """
        종목코드/이름을 받아 StockSnapshot을 반환한다.
        내부적으로 기본정보 TR + 일봉차트 TR을 순차 요청한다.
        """
        from stock_universe import resolve
        resolved_ticker, _ = resolve(ticker)
        logger.info("스냅샷 수집 시작: {} → {}", ticker, resolved_ticker)

        basic   = self._fetch_basic_info(resolved_ticker)
        daily_df = self._fetch_daily_chart(resolved_ticker, count=120)

        if daily_df.empty:
            logger.warning("일봉 데이터 없음 — 기술지표 계산 불가: {}", ticker)
            indicators = {}
        else:
            indicators = self._calc_indicators(daily_df)

        # 한국 종목이면 마이크로구조 데이터 추가 (실패 시 빈 dict)
        micro = {}
        if (resolved_ticker.endswith(".KS") or resolved_ticker.endswith(".KQ")) and \
                hasattr(self._kw, "get_market_microstructure"):
            try:
                micro = self._kw.get_market_microstructure(resolved_ticker)
            except Exception:
                pass

        # 인트라데이 멀티타임프레임 (5분/15분 RSI) — KR=키움 ka10080, 해외=yfinance
        mtf = self._compute_mtf(resolved_ticker, indicators.get("ma20", 0))

        snap = StockSnapshot(
            ticker        = resolved_ticker,
            name          = basic.get("name", ""),
            current_price = int(basic.get("current_price", 0)),
            open_price    = int(basic.get("open_price", 0)),
            high_price    = int(basic.get("high_price", 0)),
            low_price     = int(basic.get("low_price", 0)),
            volume        = int(basic.get("volume", 0)),
            volume_ratio  = float(basic.get("volume_ratio", 1.0)),
            per           = float(basic.get("per", 0.0)),
            foreigner_pct = float(basic.get("foreigner_pct", 0.0)),
            bid_qty       = int(micro.get("bid_qty", 0)),
            ask_qty       = int(micro.get("ask_qty", 0)),
            bid_ask_ratio = float(micro.get("bid_ask_ratio", 0.0)),
            foreign_net   = int(micro.get("foreign_net", 0)),
            inst_net      = int(micro.get("inst_net", 0)),
            rsi_5m        = mtf.get("rsi_5m", 0.0),
            rsi_15m       = mtf.get("rsi_15m", 0.0),
            intraday_trend= mtf.get("intraday_trend", ""),
            mtf_aligned   = mtf.get("mtf_aligned", False),
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

    def _compute_mtf(self, ticker: str, ma20_daily: float) -> dict:
        """
        인트라데이 멀티타임프레임 (5분/15분 RSI + 추세 정렬).
        - KR(.KS/.KQ): 키움 ka10080 분봉 (실시간)
        - 해외: yfinance 5분봉 (15분 지연, 키움 해외 분봉 미지원)
        """
        try:
            close5: pd.Series

            if (ticker.endswith(".KS") or ticker.endswith(".KQ")) and \
                    hasattr(self._kw, "get_minute_chart"):
                # 키움 5분봉 — 실시간
                res = self._kw.get_minute_chart(ticker, count=200, tic_scope="5")
                df = res.get("df", pd.DataFrame()) if isinstance(res, dict) else pd.DataFrame()
                if df.empty or len(df) < 30 or "close" not in df.columns:
                    return {}
                close5 = df["close"].astype(float).reset_index(drop=True)
                # 시간 인덱스를 부여해 resample 가능하게
                if "time" in df.columns:
                    try:
                        idx = pd.to_datetime(df["time"], format="%Y%m%d%H%M%S", errors="coerce")
                        if idx.notna().all():
                            close5.index = idx
                    except Exception:
                        pass
            else:
                # 해외 — yfinance fallback
                import yfinance as yf
                df = yf.download(ticker, period="5d", interval="5m",
                                 progress=False, auto_adjust=True)
                if df is None or df.empty or len(df) < 30:
                    return {}
                if hasattr(df.columns, "levels"):
                    df.columns = [c[0].lower() for c in df.columns]
                else:
                    df.columns = [c.lower() for c in df.columns]
                close5 = df["close"].astype(float)

            def _rsi(series, period=14):
                d = series.diff()
                g = d.clip(lower=0).ewm(com=period-1, min_periods=period).mean()
                l = (-d).clip(lower=0).ewm(com=period-1, min_periods=period).mean()
                rs = g / l.replace(0, float("inf"))
                rsi = 100 - 100/(1+rs)
                return float(rsi.iloc[-1]) if not rsi.isna().all() else 0.0

            rsi_5m = round(_rsi(close5), 1)
            # 15분봉 = 5분봉 3개 → resample (시간 인덱스 있을 때만)
            rsi_15m = 0.0
            if isinstance(close5.index, pd.DatetimeIndex):
                try:
                    close15 = close5.resample("15min").last().dropna()
                    if len(close15) >= 20:
                        rsi_15m = round(_rsi(close15), 1)
                except Exception:
                    pass
            else:
                # 인덱스 없으면 3개 단위 다운샘플
                try:
                    close15 = close5.iloc[::3].reset_index(drop=True)
                    if len(close15) >= 20:
                        rsi_15m = round(_rsi(close15), 1)
                except Exception:
                    pass

            # 5분봉 MA20 기준 추세
            ma5_intra = float(close5.tail(20).mean())
            cur_intra = float(close5.iloc[-1])
            if cur_intra > ma5_intra * 1.005:
                trend = "UP"
            elif cur_intra < ma5_intra * 0.995:
                trend = "DOWN"
            else:
                trend = "FLAT"

            aligned = bool(
                ma20_daily > 0 and (
                    (cur_intra > ma20_daily and trend == "UP") or
                    (cur_intra < ma20_daily and trend == "DOWN")
                )
            )
            return {
                "rsi_5m":         rsi_5m,
                "rsi_15m":        rsi_15m,
                "intraday_trend": trend,
                "mtf_aligned":    aligned,
            }
        except Exception as e:
            logger.debug("MTF 계산 실패 [{}]: {}", ticker, e)
            return {}

    def get_minute_df(self, ticker: str, count: int = 120) -> pd.DataFrame:
        """
        분봉 DataFrame을 반환한다 (REST only).
        120개 1분봉으로 120일선 계산 가능.
        """
        if self._is_rest_api:
            result = self._kw.get_minute_chart(ticker, count)
            return result.get("df", pd.DataFrame())
        # TR API: minute chart TR (구현 생략 — REST 우선)
        return pd.DataFrame()

    # ── TR 요청 (내부) ──────────────────────

    @property
    def _is_rest_api(self) -> bool:
        """KiwoomRestAPI 사용 시 True"""
        return hasattr(self._kw, "get_basic_info")

    def _fetch_basic_info(self, ticker: str) -> dict:
        """
        주식기본정보 조회.
        국내(.KS/.KQ): 키움 ka10001 → yfinance 폴백
        해외:          키움 해외 → Finnhub → yfinance (3단계 폴백)
        """
        is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")

        if self._is_rest_api:
            if is_kr:
                # 국내: 키움 → yfinance
                result = self._kw.get_basic_info(ticker)
                if result and result.get("current_price", 0) > 0:
                    return result
                logger.warning("Kiwoom REST 기본정보 실패 — yfinance 폴백: {}", ticker)
                return self._yf_basic_info(ticker)
            else:
                # 해외: 키움 해외 → Finnhub → yfinance
                return self._fetch_overseas_basic_info(ticker)

        # TR API(KiwoomAPI): 기존 콜백 방식
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

    def _yf_basic_info(self, ticker: str) -> dict:
        """yfinance로 기본정보 폴백 조회 (한국 주식만 해당 suffix 유지)"""
        import yfinance as yf
        import pandas as pd
        try:
            yf_ticker = ticker  # Korean: 005930.KS / .KQ  US/global: AAPL, MSFT etc
            is_kr = ticker.endswith((".KS", ".KQ"))
            info = yf.Ticker(yf_ticker).info
            # yfinance 1.x: info는 dict (fast_info는 1.3+에서 손상)
            if isinstance(info, dict):
                price = float(info.get("currentPrice") or info.get("regularMarketPrice") or
                             info.get("previousClose") or 0)
                if price > 0:
                    return {
                        "name":          info.get("shortName", ""),
                        "current_price": int(price) if is_kr else price,
                        "open_price":    int(float(info.get("open") or price)) if is_kr else float(info.get("open") or price),
                        "high_price":    int(float(info.get("dayHigh") or price)) if is_kr else float(info.get("dayHigh") or price),
                        "low_price":     int(float(info.get("dayLow") or price)) if is_kr else float(info.get("dayLow") or price),
                        "volume":        int(info.get("averageVolume") or 0),
                        "volume_ratio":  1.0,
                        "per":           float(info.get("trailingPE") or 0.0),
                        "foreigner_pct": float(info.get("heldPercentInstitutions", 0) or 0) * 100,
                    }
            # .info 실패 시 (rate limit 등) → yf.download로 종가 기반 폴백
            raw = yf.download(yf_ticker, period="5d", interval="1d",
                              progress=False, auto_adjust=True)
            if raw is None or raw.empty:
                return {}
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0].lower() for c in raw.columns]
            else:
                raw.columns = [c.lower() for c in raw.columns]
            price = float(raw["close"].iloc[-1])
            if price <= 0:
                return {}
            return {
                "name":          "",
                "current_price": int(price) if is_kr else price,
                "open_price":    int(float(raw["open"].iloc[-1])) if is_kr else float(raw["open"].iloc[-1]),
                "high_price":    int(float(raw["high"].iloc[-1])) if is_kr else float(raw["high"].iloc[-1]),
                "low_price":     int(float(raw["low"].iloc[-1])) if is_kr else float(raw["low"].iloc[-1]),
                "volume":        int(raw["volume"].iloc[-1]),
                "volume_ratio":  1.0,
                "per":           0.0,
                "foreigner_pct": 0.0,
            }
        except Exception as e:
            logger.error("yfinance 기본정보 실패 [{}]: {}", ticker, e)
            return {}

    # ── 해외주식 폴백 체인: 키움 → Finnhub → yfinance ──────────
    _finnhub_failed: bool = False   # Finnhub 401/403 등 영구 오류면 세션 스킵

    def _fetch_overseas_basic_info(self, ticker: str) -> dict:
        """해외주식 기본정보 — 3단계 폴백 (15분 지연 허용)"""
        # 1) 키움 해외 (지원되면)
        if hasattr(self._kw, "get_overseas_basic_info"):
            r = self._kw.get_overseas_basic_info(ticker)
            if r and r.get("current_price", 0) > 0:
                logger.debug("[해외][{}] 키움 해외 hit", ticker)
                return r
        # 2) Finnhub (API key 있고 이전에 영구 실패 안 했을 때)
        from config import FINNHUB_API_KEY
        if FINNHUB_API_KEY and not DataCollector._finnhub_failed:
            r = self._finnhub_basic_info(ticker)
            if r and r.get("current_price", 0) > 0:
                logger.debug("[해외][{}] Finnhub hit", ticker)
                return r
        # 3) yfinance (최종)
        logger.debug("[해외][{}] yfinance 폴백", ticker)
        return self._yf_basic_info(ticker)

    def _fetch_overseas_daily_chart(self, ticker: str, count: int = 60) -> pd.DataFrame:
        """해외주식 일봉 — 3단계 폴백"""
        if hasattr(self._kw, "get_overseas_daily_chart"):
            res = self._kw.get_overseas_daily_chart(ticker, count)
            df = res.get("df", pd.DataFrame())
            if not df.empty:
                return df
        from config import FINNHUB_API_KEY
        if FINNHUB_API_KEY and not DataCollector._finnhub_failed:
            df = self._finnhub_daily_chart(ticker, count)
            if df is not None and not df.empty:
                return df
        return self._yf_daily_chart(ticker, count)

    def _finnhub_basic_info(self, ticker: str) -> dict:
        """Finnhub /quote — 무료 60req/min. 응답: {c,h,l,o,pc,d,dp,t}"""
        import requests
        from config import FINNHUB_API_KEY
        try:
            r = requests.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": ticker, "token": FINNHUB_API_KEY},
                timeout=4,
            )
            if r.status_code in (401, 403):
                logger.warning("Finnhub 인증 실패 — API key 확인 (세션 스킵)")
                DataCollector._finnhub_failed = True
                return {}
            data = r.json() if r.status_code == 200 else {}
            cur = float(data.get("c") or 0)
            if cur <= 0:
                return {}
            return {
                "name":          ticker,
                "current_price": cur,
                "open_price":    float(data.get("o") or cur),
                "high_price":    float(data.get("h") or cur),
                "low_price":     float(data.get("l") or cur),
                "volume":        0,           # /quote 는 volume 없음 → 일봉에서
                "volume_ratio":  1.0,
                "per":           0.0,
                "foreigner_pct": 0.0,
            }
        except Exception as e:
            logger.debug("Finnhub quote 실패 [{}]: {}", ticker, e)
            return {}

    def _finnhub_daily_chart(self, ticker: str, count: int = 60) -> pd.DataFrame:
        """Finnhub /stock/candle — 일봉 OHLCV"""
        import requests, time as _time
        from config import FINNHUB_API_KEY
        try:
            now_ts = int(_time.time())
            from_ts = now_ts - 86400 * (count + 30)
            r = requests.get(
                "https://finnhub.io/api/v1/stock/candle",
                params={"symbol": ticker, "resolution": "D",
                        "from": from_ts, "to": now_ts, "token": FINNHUB_API_KEY},
                timeout=6,
            )
            if r.status_code in (401, 403):
                DataCollector._finnhub_failed = True
                return pd.DataFrame()
            d = r.json() if r.status_code == 200 else {}
            if d.get("s") != "ok" or not d.get("c"):
                return pd.DataFrame()
            rows = []
            for i, c in enumerate(d["c"]):
                rows.append({
                    "date":   datetime.fromtimestamp(d["t"][i]),
                    "open":   float(d["o"][i]),
                    "high":   float(d["h"][i]),
                    "low":    float(d["l"][i]),
                    "close":  float(c),
                    "volume": int(d.get("v", [0])[i] if d.get("v") else 0),
                })
            return pd.DataFrame(rows)
        except Exception as e:
            logger.debug("Finnhub candle 실패 [{}]: {}", ticker, e)
            return pd.DataFrame()

    def _fetch_daily_chart(self, ticker: str, count: int = 60) -> pd.DataFrame:
        """
        일봉차트 조회.
        국내: 키움 ka10081 → yfinance
        해외: 키움 해외 → Finnhub → yfinance
        """
        is_kr = ticker.endswith(".KS") or ticker.endswith(".KQ")

        if self._is_rest_api:
            if is_kr:
                result = self._kw.get_daily_chart(ticker, count)
                df = result.get("df", pd.DataFrame())
                if not df.empty:
                    return df
                logger.warning("Kiwoom REST 일봉 실패 — yfinance 폴백: {}", ticker)
                return self._yf_daily_chart(ticker, count)
            else:
                return self._fetch_overseas_daily_chart(ticker, count)

        # TR API(KiwoomAPI): 기존 콜백 방식
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

    def _yf_daily_chart(self, ticker: str, count: int = 60) -> pd.DataFrame:
        """yfinance로 일봉 폴백 조회"""
        import yfinance as yf
        import pandas as pd
        try:
            yf_ticker = ticker  # Korean: 005930.KS / .KQ  US/global: AAPL, MSFT etc
            raw = yf.download(yf_ticker, period="6mo", interval="1d",
                              progress=False, auto_adjust=True)
            if raw is None or len(raw) < 20:
                return pd.DataFrame()
            # Handle potential MultiIndex columns
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = [c[0].lower() for c in raw.columns]
            else:
                raw.columns = [c.lower() for c in raw.columns]
            # Convert index to date column
            raw = raw.reset_index()
            date_col = raw.columns[0]   # 'Date' (DatetimeIndex becomes a column after reset_index)
            raw["date"] = pd.to_datetime(raw[date_col]).dt.strftime("%Y%m%d")
            raw = raw.drop(columns=[date_col])
            return raw.rename(columns={"date": "date", "open": "open", "high": "high",
                                     "low": "low", "close": "close", "volume": "volume"})
        except Exception as e:
            logger.error("yfinance 일봉 실패 [{}]: {}", ticker, e)
            return pd.DataFrame()

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
        ta 라이브러리 없이 순수 pandas로 계산한다.
        """
        close  = df["close"].astype(float)
        high   = df["high"].astype(float)
        low    = df["low"].astype(float)
        volume = df["volume"].astype(float) if "volume" in df.columns else pd.Series([0]*len(df))

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

        # ── 120일 이동평균 (단타 진입 필터용) ─────
        ma120_series = close.rolling(120).mean()
        result["ma120"] = round(float(ma120_series.iloc[-1]), 0) if not ma120_series.isna().all() else 0.0

        # ── MA20 5일 기울기 % (추세 지속 판별) ─────
        # 정적 ma5>ma20 보다 정확. 평평한 ma20 위에서의 cross 는 가짜 신호.
        try:
            if len(ma20) >= 6 and ma20.iloc[-6] > 0:
                result["ma20_slope_pct"] = round(
                    (float(ma20.iloc[-1]) - float(ma20.iloc[-6])) / float(ma20.iloc[-6]) * 100, 2
                )
            else:
                result["ma20_slope_pct"] = 0.0
        except Exception:
            result["ma20_slope_pct"] = 0.0

        # ── 추세 일관성: 최근 20일 중 close>MA20 비율 (%) ─
        # 70%+ = 강한 추세 / 30%- = 약세 / 그 외 = 횡보
        try:
            window = min(20, len(close))
            if window >= 10 and not ma20.isna().all():
                last_close = close.tail(window)
                last_ma20  = ma20.tail(window)
                above = (last_close > last_ma20).sum()
                result["uptrend_consistency"] = round(float(above) / window * 100, 1)
            else:
                result["uptrend_consistency"] = 0.0
        except Exception:
            result["uptrend_consistency"] = 0.0

        # ── 매집 압력: 최근 20일 up-day 평균거래량 / down-day 평균거래량 ──
        # >1.3 = 매집 우위, <0.8 = 분산 우위. OBV 보다 직관적.
        try:
            tail_n = min(20, len(close))
            tail_close = close.tail(tail_n)
            tail_vol   = volume.tail(tail_n)
            chg = tail_close.diff()
            up_vol   = tail_vol.where(chg > 0, 0).sum()
            down_vol = tail_vol.where(chg < 0, 0).sum()
            up_days   = max(int((chg > 0).sum()), 1)
            down_days = max(int((chg < 0).sum()), 1)
            up_avg   = up_vol   / up_days
            down_avg = down_vol / down_days
            if down_avg > 0:
                result["accumulation_ratio"] = round(float(up_avg / down_avg), 2)
            else:
                result["accumulation_ratio"] = 2.0  # 하락일 거래 거의 없음 = 매집
        except Exception:
            result["accumulation_ratio"] = 1.0

        # ── ATR(14) — True Range 이동평균 (동적 SL/TP) ──
        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low  - prev_close).abs(),
        ], axis=1).max(axis=1)
        atr_series = tr.rolling(14).mean()
        last_close = float(close.iloc[-1])
        atr_val = float(atr_series.iloc[-1]) if not atr_series.isna().all() else 0.0
        result["atr"] = round(atr_val, 2)
        result["atr_pct"] = round(atr_val / last_close * 100, 3) if last_close > 0 else 0.0

        # ── MFI(14) — Money Flow Index (거래대금 가중 RSI) ────
        # RSI 보다 정확. 펌프 (가격↑ + 거래량 X) vs 진짜 매집 구분.
        try:
            tp = (high + low + close) / 3                        # typical price
            mf = tp * volume                                     # money flow
            pos_mf = mf.where(tp > tp.shift(1), 0).rolling(14).sum()
            neg_mf = mf.where(tp < tp.shift(1), 0).rolling(14).sum()
            mfi_ratio = pos_mf / neg_mf.replace(0, 1e-9)
            mfi_series = 100 - (100 / (1 + mfi_ratio))
            mfi_val = float(mfi_series.iloc[-1]) if not mfi_series.isna().all() else 0.0
            result["mfi"] = round(mfi_val, 1)
        except Exception:
            result["mfi"] = 0.0

        # ── 52주 위치 (신고가/신저가 대비 거리) ────────────────
        # 데이터가 252일 부족하면 가용한 만큼 사용 (최소 60일)
        try:
            window = min(len(close), 252)
            if window >= 60:
                hi_52w = float(close.iloc[-window:].max())
                lo_52w = float(close.iloc[-window:].min())
                result["dist_from_52w_high"] = round((last_close - hi_52w) / hi_52w * 100, 2) if hi_52w > 0 else 0.0
                result["dist_from_52w_low"]  = round((last_close - lo_52w) / lo_52w * 100, 2) if lo_52w > 0 else 0.0
            else:
                result["dist_from_52w_high"] = 0.0
                result["dist_from_52w_low"]  = 0.0
        except Exception:
            result["dist_from_52w_high"] = 0.0
            result["dist_from_52w_low"]  = 0.0

        # ── 일평균 거래대금 (유동성 지표) ──────────────────────
        # close × volume 의 최근 20일 평균
        try:
            value_series = close * volume
            avg_value = float(value_series.tail(20).mean())
            result["value_traded"] = round(avg_value, 0)
        except Exception:
            result["value_traded"] = 0.0

        # ── OBV(On-Balance Volume) 20일 변화율 ─────────────────
        # 가격 ↑ 봉 거래량 + / 가격 ↓ 봉 거래량 -. 누적 흐름의 모멘텀.
        try:
            sign = (close.diff().fillna(0) > 0).astype(int) - (close.diff().fillna(0) < 0).astype(int)
            obv = (sign * volume).cumsum()
            if len(obv) >= 20:
                obv_now  = float(obv.iloc[-1])
                obv_past = float(obv.iloc[-20])
                if obv_past != 0:
                    result["obv_trend"] = round((obv_now - obv_past) / abs(obv_past) * 100, 2)
                else:
                    result["obv_trend"] = 0.0
            else:
                result["obv_trend"] = 0.0
        except Exception:
            result["obv_trend"] = 0.0
        # OBV 시리즈 (다이버전스 판별용 — 위에서 계산했으면 재사용)
        try:
            sign_d = (close.diff().fillna(0) > 0).astype(int) - (close.diff().fillna(0) < 0).astype(int)
            obv_series = (sign_d * volume).cumsum()
        except Exception:
            obv_series = None

        # ── BB Squeeze 감지 (변동성 폭발 임박 신호) ────────
        # bb_width = (upper - lower) / middle. 60일 평균 대비 60% 미만 = squeeze
        try:
            ma20_s   = close.rolling(20).mean()
            std20    = close.rolling(20).std()
            bb_upper = ma20_s + 2 * std20
            bb_lower = ma20_s - 2 * std20
            bb_width_series = (bb_upper - bb_lower) / ma20_s.replace(0, 1e-9) * 100
            cur_width = float(bb_width_series.iloc[-1]) if not bb_width_series.isna().all() else 0.0
            avg_width = float(bb_width_series.tail(60).mean()) if len(bb_width_series) >= 60 else cur_width
            result["bb_width_pct"] = round(cur_width, 2)
            result["bb_squeeze"]   = bool(cur_width > 0 and avg_width > 0 and (cur_width / avg_width) < 0.6)
        except Exception:
            result["bb_width_pct"] = 0.0
            result["bb_squeeze"]   = False

        # ── RSI 다이버전스 (최근 20일 가격/RSI 신저-신고 비교) ───
        try:
            window = 20
            if len(close) >= window:
                tail_close = close.tail(window)
                tail_rsi   = rsi.tail(window)
                # 가격이 windowmin 갱신 + RSI 가 직전 저점보다 높음 → bullish div
                px_min_now  = float(tail_close.min())
                px_now      = float(tail_close.iloc[-1])
                rsi_min_idx = int(tail_close.idxmin().value if hasattr(tail_close.idxmin(),'value') else tail_close.values.argmin())
                # 단순화: 최근 가격 신저가(<=현재가) + RSI 가 이전 저점보다 더 높으면 bullish
                bullish_div = bool(
                    px_now <= px_min_now * 1.005   # 가격 거의 신저가
                    and float(tail_rsi.iloc[-1]) > float(tail_rsi.min()) + 5
                )
                # 베어리시: 가격 신고가 근처 + RSI 는 직전 고점보다 낮음
                px_max_now  = float(tail_close.max())
                bearish_div = bool(
                    px_now >= px_max_now * 0.995
                    and float(tail_rsi.iloc[-1]) < float(tail_rsi.max()) - 5
                )
                result["bull_div_rsi"] = bullish_div
                result["bear_div_rsi"] = bearish_div
            else:
                result["bull_div_rsi"] = False
                result["bear_div_rsi"] = False
        except Exception:
            result["bull_div_rsi"] = False
            result["bear_div_rsi"] = False

        # ── OBV 다이버전스 (가격↓ vs OBV↑ / 가격↑ vs OBV↓) ──
        try:
            if obv_series is not None and len(obv_series) >= 20:
                px_chg  = (float(close.iloc[-1])      - float(close.iloc[-20]))      / float(close.iloc[-20]) * 100 if close.iloc[-20] else 0
                obv_chg = (float(obv_series.iloc[-1]) - float(obv_series.iloc[-20])) / abs(float(obv_series.iloc[-20]) or 1) * 100
                # 의미 있는 차이 (5% 이상 반대)
                result["bull_div_obv"] = bool(px_chg < -3 and obv_chg > 3)
                result["bear_div_obv"] = bool(px_chg > 3  and obv_chg < -3)
            else:
                result["bull_div_obv"] = False
                result["bear_div_obv"] = False
        except Exception:
            result["bull_div_obv"] = False
            result["bear_div_obv"] = False

        # ── ADX(14) — 추세 강도 + 방향 (DMI 시스템) ──
        try:
            up_move   = high.diff()
            down_move = -low.diff()
            plus_dm   = up_move.where((up_move > down_move) & (up_move > 0), 0).fillna(0)
            minus_dm  = down_move.where((down_move > up_move) & (down_move > 0), 0).fillna(0)
            atr_dmi   = tr.rolling(14).sum().replace(0, 1e-9)
            plus_di_s = (plus_dm.rolling(14).sum() / atr_dmi) * 100
            minus_di_s= (minus_dm.rolling(14).sum() / atr_dmi) * 100
            dx = (abs(plus_di_s - minus_di_s) / (plus_di_s + minus_di_s).replace(0, 1e-9)) * 100
            adx_s = dx.rolling(14).mean()
            result["adx"]      = round(float(adx_s.iloc[-1]) if not adx_s.isna().all() else 0.0, 1)
            result["plus_di"]  = round(float(plus_di_s.iloc[-1]) if not plus_di_s.isna().all() else 0.0, 1)
            result["minus_di"] = round(float(minus_di_s.iloc[-1]) if not minus_di_s.isna().all() else 0.0, 1)
        except Exception:
            result["adx"] = result["plus_di"] = result["minus_di"] = 0.0

        # ── Williams %R (14) — Stochastic 변형, 빠른 반전 ──
        try:
            ll14 = low.rolling(14).min()
            hh14 = high.rolling(14).max()
            wr = -100 * (hh14 - close) / (hh14 - ll14).replace(0, 1e-9)
            result["williams_r"] = round(float(wr.iloc[-1]) if not wr.isna().all() else 0.0, 1)
        except Exception:
            result["williams_r"] = 0.0

        # ── Force Index (13 EMA) — 가격×거래량 모멘텀 (Elder) ──
        try:
            fi_raw = (close - close.shift(1)) * volume
            fi_ema = fi_raw.ewm(span=13, adjust=False).mean()
            fi_val = float(fi_ema.iloc[-1]) if not fi_ema.isna().all() else 0.0
            # 정규화 (가격 단위 차이 흡수): 절대치 / 평균거래대금
            value_avg = float((close * volume).tail(20).mean()) or 1.0
            result["force_index"] = round(fi_val / value_avg * 100, 2)
        except Exception:
            result["force_index"] = 0.0

        # ── CMF(20) — Chaikin Money Flow ──────────
        try:
            mfm = ((close - low) - (high - close)) / (high - low).replace(0, 1e-9)
            mfv = mfm * volume
            cmf_s = mfv.rolling(20).sum() / volume.rolling(20).sum().replace(0, 1e-9)
            result["cmf"] = round(float(cmf_s.iloc[-1]) if not cmf_s.isna().all() else 0.0, 3)
        except Exception:
            result["cmf"] = 0.0

        # ── Ichimoku Cloud (한국 시장 표준) ───────
        try:
            # 전환선 9, 기준선 26, 선행1=평균(전환,기준), 선행2=중간점 52, 후행=현재가
            tenkan = (high.rolling(9).max() + low.rolling(9).min()) / 2
            kijun  = (high.rolling(26).max() + low.rolling(26).min()) / 2
            span_a = ((tenkan + kijun) / 2)         # 26봉 후행 비교 (현재 시점 비교)
            span_b = (high.rolling(52).max() + low.rolling(52).min()) / 2
            top    = float(max(span_a.iloc[-1], span_b.iloc[-1]))
            bot    = float(min(span_a.iloc[-1], span_b.iloc[-1]))
            cur    = float(close.iloc[-1])
            result["above_cloud"] = bool(cur > top)
            result["below_cloud"] = bool(cur < bot)
            result["cloud_thick"] = round((top - bot) / cur * 100, 2) if cur else 0.0
        except Exception:
            result["above_cloud"] = result["below_cloud"] = False
            result["cloud_thick"] = 0.0

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
            # KRW: 정수 정밀도, USD/기타: 소수점 2자리 유지
            def _round_price(p: float) -> float:
                return float(int(p)) if is_kr else round(p, 2)

            price: float | None = None
            try:
                from core.price_fetcher import get_current_price
                gf_price = get_current_price(ticker)
                if gf_price and gf_price > 0:
                    price = _round_price(gf_price)
            except Exception:
                pass
            if not price:
                try:
                    info = yf.Ticker(ticker).info
                    if isinstance(info, dict):
                        p = float(info.get("currentPrice") or info.get("regularMarketPrice") or 0)
                        if p > 0:
                            price = _round_price(p)
                except Exception:
                    pass
            if not price:
                price = _round_price(float(close_s.iloc[-1]))

            vol_avg   = float(volume_s.iloc[-20:-1].mean())
            vol_today = float(volume_s.iloc[-1])
            vol_ratio = round(vol_today / vol_avg, 2) if vol_avg > 0 else 1.0

            # _calc_indicators expects lowercase columns
            indicators = DataCollector._calc_indicators(df)

            from stock_universe import get_name
            name = get_name(ticker)

            def _ohlc(col: str) -> float:
                return _round_price(float(df[col].iloc[-1])) if col in df.columns else price

            return StockSnapshot(
                ticker        = ticker,
                name          = name,
                current_price = price,
                open_price    = _ohlc("open"),
                high_price    = _ohlc("high"),
                low_price     = _ohlc("low"),
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
