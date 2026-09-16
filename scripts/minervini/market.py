"""시장 국면(Market Regime) 판단.

미너비니는 "시장과 싸우지 말라"고 반복한다. 개별 종목이 아무리 좋아도
지수가 하락 추세면 돌파는 대부분 실패한다. 그래서 스크리닝 결과를 보기 전에
지수 추세 + 시장 폭(breadth)으로 노출도를 먼저 정한다.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .indicators import sma, slope_up


QUAD_MONTHS = (3, 6, 9, 12)   # 쿼드러플 위칭 — 선물·옵션 네 종류가 같이 만기


def monthly_opex(year: int, month: int) -> pd.Timestamp:
    """미국 월물 옵션 만기일 = 그 달 세 번째 금요일."""
    fridays = [d for d in pd.date_range(f"{year}-{month:02d}-01", periods=31, freq="D")
               if d.month == month and d.weekday() == 4]
    return pd.Timestamp(fridays[2])


def _adjust_holiday(cand: pd.Timestamp, idx) -> pd.Timestamp:
    """세 번째 금요일이 휴장(성금요일 등)이면 직전 거래일로 당긴다.

    달력이 그 날짜까지 덮고 있을 때만 판단한다. 아직 오지 않은 날은 휴장인지 알 수 없으므로
    그대로 둔다 (데이터 마지막 날로 당겨버리는 실수를 막는다)."""
    if idx is None or not len(idx):
        return cand
    idx = pd.DatetimeIndex(idx).normalize()
    if cand > idx[-1] or cand in idx:
        return cand
    prev = idx[idx <= cand]
    return prev[-1] if len(prev) else cand


def next_opex(today, trading_days=None) -> pd.Timestamp:
    """오늘 이후(당일 포함) 가장 가까운 만기일."""
    today = pd.Timestamp(today).normalize()
    cand = _adjust_holiday(monthly_opex(today.year, today.month), trading_days)
    if cand < today:
        nxt = today + pd.DateOffset(months=1)
        cand = _adjust_holiday(monthly_opex(nxt.year, nxt.month), trading_days)
    return cand


def opex_info(today, trading_days=None) -> dict:
    """다음 만기일까지 남은 거래일 수와 쿼드위칭 여부."""
    today = pd.Timestamp(today).normalize()
    date = next_opex(today, trading_days)
    idx = pd.DatetimeIndex(trading_days).normalize() if trading_days is not None and len(trading_days) else None
    if idx is not None and date <= idx[-1]:
        left = int(((idx > today) & (idx <= date)).sum())
    else:
        # 달력 밖(아직 안 온 날)은 영업일로 센다 — 휴일은 반영 못 하지만 하루 차이 수준
        left = int(np.busday_count(today.date(), date.date()))
    return {"date": date, "days": left, "quad": date.month in QUAD_MONTHS,
            "near": left <= 5}


@dataclass
class MarketRegime:
    symbol: str = "SPY"
    price: float = np.nan
    ma50: float = np.nan
    ma200: float = np.nan
    above_ma50: bool = False
    above_ma200: bool = False
    ma200_up: bool = False
    pct_from_high: float = np.nan
    breadth_stage2: float = np.nan   # 유니버스 중 Trend Template 통과 비율 %
    breadth_above_ma200: float = np.nan
    opex_date = None         # 다음 옵션 만기일 (매월 세 번째 금요일)
    opex_days: int = -1      # 만기까지 남은 거래일
    opex_quad: bool = False  # 쿼드러플 위칭 (3·6·9·12월)
    light: str = "회색불"
    exposure: str = "-"
    comment: str = ""


def analyze(bench: pd.DataFrame | None, breadth_stage2: float, breadth_above_ma200: float, symbol: str = "SPY") -> MarketRegime:
    r = MarketRegime(symbol=symbol, breadth_stage2=breadth_stage2, breadth_above_ma200=breadth_above_ma200)
    if bench is None or len(bench) < 220:
        r.comment = "지수 데이터 부족 — 국면 판단 생략"
        return r

    info = opex_info(bench.index[-1], bench.index)
    r.opex_date, r.opex_days, r.opex_quad = info["date"], info["days"], info["quad"]

    close = bench["Close"].dropna()
    ma50, ma200 = sma(close, 50), sma(close, 200)
    r.price = float(close.iloc[-1])
    r.ma50, r.ma200 = float(ma50.iloc[-1]), float(ma200.iloc[-1])
    r.above_ma50 = r.price > r.ma50
    r.above_ma200 = r.price > r.ma200
    r.ma200_up = slope_up(ma200, 20)
    high52 = float(bench["High"].iloc[-252:].max())
    r.pct_from_high = (r.price / high52 - 1.0) * 100.0

    score = sum([r.above_ma50, r.above_ma200, r.ma200_up, r.ma50 > r.ma200])

    if score == 4 and breadth_stage2 >= 25:
        r.light, r.exposure = "초록불", "공격 (75~100%)"
        r.comment = "지수 추세·시장 폭 모두 양호. 돌파 매수에 유리한 구간."
    elif score >= 3 and breadth_stage2 >= 15:
        r.light, r.exposure = "노란불", "선별 (40~70%)"
        r.comment = "추세는 살아 있으나 폭이 좁다. 최상위 셋업만, 비중은 줄여서."
    elif score >= 2:
        r.light, r.exposure = "주황불", "축소 (20~40%)"
        r.comment = "혼조 국면. 돌파 실패율이 올라간다. 손절을 더 타이트하게."
    else:
        r.light, r.exposure = "빨간불", "방어 (0~20%)"
        r.comment = "지수가 200일선 아래이거나 추세 훼손. 현금 비중을 높이고 관찰만."

    if breadth_stage2 < 10:
        r.comment += " (Stage 2 종목이 10% 미만 — 시장 폭 경고)"
    return r
