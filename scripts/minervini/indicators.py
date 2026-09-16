"""기술적 지표 계산."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """지수이동평균 (Pine ta.ema 와 같은 alpha = 2/(n+1))."""
    return series.ewm(span=span, adjust=False, min_periods=span).mean()


def up_down_volume_ratio(df: pd.DataFrame, window: int = 50) -> float:
    """U/D 거래량 비율 = 상승일 거래량 합 ÷ 하락일 거래량 합 (최근 window봉).

    미너비니가 '기관 매집'을 확인할 때 보는 값이다. 기관은 하루에 다 못 사서
    오르는 날 거래량을 키우며 여러 날에 걸쳐 모은다.

    S&P 500 5년 실측(20일 신고가+대량 돌파 9,499건의 20일 뒤 수익률):
      U/D 2.0 이상 825건 +3.40% vs 나머지 +1.31% (90% 구간 안 겹침)
      U/D 1.5 이상 +2.04% vs +1.23% (구간이 살짝 겹침)
      흔히 쓰는 1.0 기준은 차이가 없었다 (오히려 -0.44%p). 그래서 기본 기준을 1.5로 뒀다.
    """
    if df is None or len(df) < window + 1:
        return float("nan")
    close = df["Close"].astype(float)
    vol = df["Volume"].astype(float)
    change = close.diff()
    up = vol.where(change > 0, 0.0).iloc[-window:].sum()
    down = vol.where(change < 0, 0.0).iloc[-window:].sum()
    if down <= 0:
        return float("nan") if up <= 0 else 99.0
    return float(up / down)


def atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    """Wilder ATR."""
    high, low, close = df["High"], df["Low"], df["Close"]
    prev = close.shift(1)
    tr = pd.concat([high - low, (high - prev).abs(), (low - prev).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()


def atr_pct(df: pd.DataFrame, window: int = 14) -> pd.Series:
    return atr(df, window) / df["Close"] * 100.0


def roc(series: pd.Series, periods: int) -> float:
    """단순 수익률 %. 데이터가 모자라면 NaN."""
    if len(series) <= periods:
        return float("nan")
    past = series.iloc[-periods - 1]
    if not np.isfinite(past) or past <= 0:
        return float("nan")
    return (series.iloc[-1] / past - 1.0) * 100.0


def rs_score(close: pd.Series, periods=(63, 126, 189, 252), weights=(0.4, 0.2, 0.2, 0.2)) -> float:
    """IBD 스타일 가중 상대강도 원점수.

    최근 분기에 40%, 나머지 세 분기에 각 20% 가중.
    """
    total_w, acc = 0.0, 0.0
    for p, w in zip(periods, weights):
        r = roc(close, p)
        if np.isfinite(r):
            acc += r * w
            total_w += w
    if total_w == 0:
        return float("nan")
    return acc / total_w  # 데이터가 짧은 종목도 비교 가능하도록 정규화


def percentile_rating(scores: pd.Series) -> pd.Series:
    """원점수를 유니버스 내 1~99 백분위 등급으로 변환 (IBD RS Rating 방식)."""
    ranks = scores.rank(pct=True, na_option="keep") * 100.0
    return ranks.clip(lower=1, upper=99).round(0)


def slope_up(series: pd.Series, lookback: int) -> bool:
    """현재값이 lookback 봉 전보다 높은가 (= 우상향)."""
    if len(series.dropna()) <= lookback:
        return False
    cur, past = series.iloc[-1], series.iloc[-1 - lookback]
    return bool(np.isfinite(cur) and np.isfinite(past) and cur > past)


def true_price_tightness(df: pd.DataFrame, fast: int = 3, slow: int = 60) -> float:
    """최근 3일 평균 캔들 스프레드가 지난 60일 분포에서 몇 %ile인지 (0~100).

    20 이하면 '극단적으로 타이트'한 구간.
    """
    spread = (df["High"] - df["Low"]) / df["Close"] * 100.0
    if len(spread.dropna()) < slow:
        return float("nan")
    recent = spread.iloc[-fast:].mean()
    window = spread.iloc[-slow:]
    return float((window < recent).mean() * 100.0)
