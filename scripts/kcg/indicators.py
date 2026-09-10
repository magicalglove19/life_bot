"""기술적 지표와 캔들 형태 판정."""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, window: int) -> pd.Series:
    """단순 이동평균. 봉이 모자라면 있는 만큼으로 계산한다(신규 상장 대응)."""
    return series.rolling(window, min_periods=max(2, window // 2)).mean()


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """일봉 DataFrame에 5/10/20/60일선과 거래량 평균을 붙인다."""
    out = df.copy()
    close = out["Close"].astype(float)
    vol = out["Volume"].astype(float)
    for w in (5, 10, 20, 60):
        out[f"ma{w}"] = sma(close, w)
    out["vol5"] = sma(vol, 5)
    out["vol20"] = sma(vol, 20)
    out["chg"] = close.pct_change() * 100.0
    out["value"] = close * vol          # 거래대금
    return out


def body_pct(row) -> float:
    """캔들 몸통 크기 (시가 대비 %). 양수면 양봉."""
    o = float(row["Open"])
    if o <= 0:
        return float("nan")
    return (float(row["Close"]) - o) / o * 100.0


def range_pct(row) -> float:
    """캔들 전체 길이 (종가 대비 %)."""
    c = float(row["Close"])
    if c <= 0:
        return float("nan")
    return (float(row["High"]) - float(row["Low"])) / c * 100.0


def body_ratio(row) -> float:
    """몸통 / 캔들 전체 길이. 1에 가까울수록 꼬리 없는 장대봉."""
    rng = float(row["High"]) - float(row["Low"])
    if rng <= 0:
        return 0.0
    return abs(float(row["Close"]) - float(row["Open"])) / rng


def is_bull(row) -> bool:
    return float(row["Close"]) > float(row["Open"])


def is_bear(row) -> bool:
    return float(row["Close"]) < float(row["Open"])


def is_doji(row, max_body: float) -> bool:
    """몸통이 거의 없는 짧은 캔들. 방향(양/음)은 따지지 않는다."""
    b = body_pct(row)
    return bool(np.isfinite(b) and abs(b) <= max_body)


def gap_pct(price: float, ma: float) -> float:
    """이격도 — 이동평균 대비 현재가가 몇 % 위인가."""
    if not np.isfinite(ma) or ma <= 0:
        return float("nan")
    return (price / ma - 1.0) * 100.0


def above_ma(row, ma_col: str, tolerance: float = 0.0) -> bool:
    """종가가 이동평균 위인가. tolerance %까지는 밑돌아도 '지지'로 본다."""
    ma = float(row.get(ma_col, np.nan))
    if not np.isfinite(ma) or ma <= 0:
        return False
    return float(row["Close"]) >= ma * (1.0 - tolerance / 100.0)


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))
