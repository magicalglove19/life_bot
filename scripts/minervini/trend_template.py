"""미너비니 Trend Template — 8개 기준으로 Stage 2 상승추세를 판정한다.

출처: Mark Minervini, 『Trade Like a Stock Market Wizard』
  1. 현재가 > 150일선 & 200일선
  2. 150일선 > 200일선
  3. 200일선이 최소 1개월 이상 우상향
  4. 50일선 > 150일선 & 200일선
  5. 현재가 > 50일선
  6. 현재가가 52주 저점보다 최소 30% 위
  7. 현재가가 52주 고점의 25% 이내
  8. RS 등급 70 이상 (80~90대 선호)
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import TrendTemplateConfig
from .indicators import sma, slope_up

CRITERIA_LABELS = [
    "1. 주가 > 150일선·200일선",
    "2. 150일선 > 200일선",
    "3. 200일선 우상향",
    "4. 50일선 > 150일선·200일선",
    "5. 주가 > 50일선",
    "6. 52주 저점 대비 +30% 이상",
    "7. 52주 고점 대비 -25% 이내",
    "8. RS 등급 70 이상",
]


@dataclass
class TrendResult:
    ticker: str
    ok: bool = False
    passed: int = 0
    checks: list[bool] = field(default_factory=list)
    reason: str = ""
    metrics: dict = field(default_factory=dict)


def compute_metrics(ticker: str, df: pd.DataFrame, cfg: TrendTemplateConfig) -> dict | None:
    """Trend Template 판정에 필요한 값들을 미리 계산한다. RS 등급은 나중에 주입."""
    close = df["Close"].dropna()
    if len(close) < cfg.ma_long + cfg.ma_long_slope_lookback:
        return None

    ma50 = sma(close, cfg.ma_short)
    ma150 = sma(close, cfg.ma_mid)
    ma200 = sma(close, cfg.ma_long)

    win = df.iloc[-cfg.week52_bars :] if len(df) >= cfg.week52_bars else df
    high52 = float(win["High"].max())
    low52 = float(win["Low"].min())
    high52_date = win["High"].idxmax()
    price = float(close.iloc[-1])

    vol50 = float(df["Volume"].iloc[-50:].mean()) if len(df) >= 50 else float(df["Volume"].mean())

    return {
        "ticker": ticker,
        "price": price,
        "ma50": float(ma50.iloc[-1]),
        "ma150": float(ma150.iloc[-1]),
        "ma200": float(ma200.iloc[-1]),
        "ma200_up": slope_up(ma200, cfg.ma_long_slope_lookback),
        "high52": high52,
        "low52": low52,
        "high52_date": high52_date,
        "pct_from_low": (price / low52 - 1.0) * 100.0 if low52 > 0 else np.nan,
        "pct_from_high": (price / high52 - 1.0) * 100.0 if high52 > 0 else np.nan,
        "avg_vol50": vol50,
        "dollar_vol50": vol50 * price,
    }


def evaluate(m: dict, rs_rating: float, cfg: TrendTemplateConfig) -> TrendResult:
    """계산된 지표 + RS 등급으로 8개 기준을 판정한다."""
    price = m["price"]
    checks = [
        price > m["ma150"] and price > m["ma200"],
        m["ma150"] > m["ma200"],
        bool(m["ma200_up"]),
        m["ma50"] > m["ma150"] and m["ma50"] > m["ma200"],
        price > m["ma50"],
        m["pct_from_low"] >= cfg.min_pct_above_52w_low,
        m["pct_from_high"] >= -cfg.max_pct_below_52w_high,
        bool(np.isfinite(rs_rating)) and rs_rating >= cfg.min_rs_rating,
    ]
    passed = int(sum(checks))
    failed = [CRITERIA_LABELS[i] for i, c in enumerate(checks) if not c]
    return TrendResult(
        ticker=m["ticker"],
        ok=all(checks),
        passed=passed,
        checks=checks,
        reason="; ".join(failed),
        metrics=m,
    )
