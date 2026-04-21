"""TQQQ/SOXL/TECL/SCHD RSI(14) 계산.
morning_digest.py에서 import해서 사용.
"""
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

TICKERS = ["TQQQ", "SOXL", "TECL", "SCHD"]
RSI_PERIOD = 14


def rsi(series: pd.Series, period: int = RSI_PERIOD) -> float:
    """Wilder RSI 계산. NaN이면 None."""
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, pd.NA)
    rsi_series = 100 - (100 / (1 + rs))
    last = rsi_series.iloc[-1]
    return float(last) if pd.notna(last) else None


def fetch_rsi(ticker: str) -> dict:
    try:
        data = yf.download(ticker, period="3mo", progress=False, auto_adjust=True, threads=False)
        if data is None or data.empty:
            return {"ticker": ticker, "rsi": None, "price": None, "change_pct": None, "error": "데이터 없음"}
        # yf.download가 MultiIndex를 반환할 수 있음
        if isinstance(data.columns, pd.MultiIndex):
            close = data["Close"][ticker] if ticker in data["Close"].columns else data["Close"].iloc[:, 0]
        else:
            close = data["Close"]
        val = rsi(close)
        last_price = float(close.iloc[-1])
        prev_price = float(close.iloc[-2]) if len(close) >= 2 else last_price
        change_pct = (last_price - prev_price) / prev_price * 100 if prev_price else 0.0
        return {
            "ticker": ticker,
            "rsi": val,
            "price": last_price,
            "change_pct": change_pct,
            "error": None,
        }
    except Exception as e:
        return {"ticker": ticker, "rsi": None, "price": None, "change_pct": None, "error": str(e)[:80]}


def rsi_emoji(v: float | None) -> str:
    if v is None:
        return "⚪"
    if v >= 70:
        return "🔴"  # 과매수
    if v <= 30:
        return "🟢"  # 과매도
    if v >= 60:
        return "🟠"
    if v <= 40:
        return "🟡"
    return "⚪"


def build_report() -> str:
    lines = ["<b>📊 RSI (14) — ETF 4종</b>"]
    for t in TICKERS:
        r = fetch_rsi(t)
        if r["error"]:
            lines.append(f"  {t}: 조회 실패 ({r['error']})")
            continue
        emoji = rsi_emoji(r["rsi"])
        rsi_str = f"{r['rsi']:.1f}" if r["rsi"] is not None else "N/A"
        lines.append(
            f"  {emoji} <b>{t}</b> RSI {rsi_str} · ${r['price']:.2f} ({r['change_pct']:+.2f}%)"
        )
    lines.append("  <i>🔴 ≥70 과매수 · 🟢 ≤30 과매도</i>")
    return "\n".join(lines)


if __name__ == "__main__":
    # 단독 실행 시 텔레그램으로 RSI만 전송 (테스트용)
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from common import telegram
    telegram.send(build_report())
