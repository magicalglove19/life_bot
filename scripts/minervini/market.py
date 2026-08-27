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
    light: str = "회색불"
    exposure: str = "-"
    comment: str = ""


def analyze(bench: pd.DataFrame | None, breadth_stage2: float, breadth_above_ma200: float, symbol: str = "SPY") -> MarketRegime:
    r = MarketRegime(symbol=symbol, breadth_stage2=breadth_stage2, breadth_above_ma200=breadth_above_ma200)
    if bench is None or len(bench) < 220:
        r.comment = "지수 데이터 부족 — 국면 판단 생략"
        return r

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
