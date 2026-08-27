"""기술적 지표 계산."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window, min_periods=window).mean()


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
