"""'언제부터인가'를 계산한다.

실행 기록을 남기는 방식은 사용자가 프로그램을 돌린 날만 알 수 있어서 부정확하다.
대신 이미 받아둔 과거 가격으로 **각 날짜에서 기준을 다시 판정**해서
Stage 2에 언제 진입했는지, VCP 셋업이 언제 나타났는지를 역산한다.
프로그램을 오늘 처음 돌려도 정확한 날짜가 나온다.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import Config


def rs_rating_matrix(closes: pd.DataFrame, periods, weights) -> pd.DataFrame:
    """날짜 × 종목 종가 행렬에서 날짜별 RS 등급(1~99) 행렬을 만든다.

    각 날짜마다 유니버스 전체를 가로로 줄 세워 백분위를 매긴다.
    """
    acc = None
    total_w = 0.0
    for p, w in zip(periods, weights):
        roc = (closes / closes.shift(p) - 1.0) * 100.0
        acc = roc * w if acc is None else acc + roc * w
        total_w += w
    raw = acc / total_w
    rating = raw.rank(axis=1, pct=True) * 100.0
    return rating.clip(lower=1, upper=99)


def trend_template_series(df: pd.DataFrame, rs: pd.Series, cfg: Config) -> pd.Series:
    """날짜별로 Trend Template 8개 기준을 모두 통과했는지(bool 시리즈)."""
    t = cfg.trend
    close = df["Close"]
    ma50 = close.rolling(t.ma_short, min_periods=t.ma_short).mean()
    ma150 = close.rolling(t.ma_mid, min_periods=t.ma_mid).mean()
    ma200 = close.rolling(t.ma_long, min_periods=t.ma_long).mean()
    high52 = df["High"].rolling(t.week52_bars, min_periods=t.week52_bars // 2).max()
    low52 = df["Low"].rolling(t.week52_bars, min_periods=t.week52_bars // 2).min()

    rs = rs.reindex(df.index)
    ok = (
        (close > ma150)
        & (close > ma200)
        & (ma150 > ma200)
        & (ma200 > ma200.shift(t.ma_long_slope_lookback))
        & (ma50 > ma150)
        & (ma50 > ma200)
        & (close > ma50)
        & (close >= low52 * (1 + t.min_pct_above_52w_low / 100.0))
        & (close >= high52 * (1 - t.max_pct_below_52w_high / 100.0))
        & (rs >= t.min_rs_rating)
    )
    return ok.fillna(False)


def streak(flags: pd.Series) -> tuple:
    """마지막 True가 며칠째 이어지고 있는지. (시작 날짜, 연속 거래일 수)."""
    if flags is None or len(flags) == 0 or not bool(flags.iloc[-1]):
        return None, 0
    arr = flags.to_numpy(dtype=bool)
    i = len(arr) - 1
    while i > 0 and arr[i - 1]:
        i -= 1
    return flags.index[i], len(arr) - i


def vcp_streak(df: pd.DataFrame, cfg: Config, max_back: int = 60) -> tuple:
    """VCP 셋업이 언제부터 잡히기 시작했는지 역산. (시작 날짜, 연속 거래일 수).

    과거 각 날짜에서 detect()를 다시 돌린다. 후보 종목만 대상이라 비용이 크지 않다.
    """
    from .vcp import detect

    n = len(df)
    if n < 60:
        return None, 0
    days = 0
    start = None
    for back in range(0, min(max_back, n - 60)):
        sub = df.iloc[: n - back]
        if not detect(sub, cfg.vcp).is_vcp:
            break
        days += 1
        start = sub.index[-1]
    return start, days


def build_close_matrix(frames: dict, tickers: list) -> pd.DataFrame:
    """{티커: OHLCV} 딕셔너리에서 날짜 × 종목 종가 행렬을 만든다."""
    cols = {t: frames[t]["Close"] for t in tickers if t in frames}
    if not cols:
        return pd.DataFrame()
    return pd.DataFrame(cols).sort_index()


def fmt(ts, days: int = 0) -> str:
    """2026-06-25 (43일째) 형태로."""
    if ts is None:
        return "-"
    d = pd.Timestamp(ts).strftime("%Y-%m-%d")
    return f"{d} ({days}일째)" if days else d


def fmt_short(ts) -> str:
    return "-" if ts is None else pd.Timestamp(ts).strftime("%m/%d")
