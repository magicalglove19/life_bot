"""미국장 마감 후 — 컵위드핸들/더블바텀/V라인/갭상승 4종 차트 패턴 스캔.
로컬 'Chart Pattern Analyzer' 앱(pattern_detectors.py 기준 동일 로직)을 헤드리스로 이식.
시가총액 상위 종목(data/us_top400.json)에서 최근 1거래일 내 확정된 패턴만 표시.
morning_digest.py에서 import해서 사용.
"""
import sys
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import refresh_tickers
from pattern_detectors import scan_dataframe

LOOKBACK_DAYS = 1
MAX_ITEMS = 25
PATTERN_EMOJI = {
    "컵위드핸들": "☕",
    "더블 바텀": "〰️",
    "V라인": "📉",
    "갭 상승": "🚀",
}


def scan_all(tickers: list[str]) -> list[dict]:
    if not tickers:
        return []
    data = yf.download(
        tickers=" ".join(tickers),
        period="1y",
        interval="1d",
        group_by="ticker",
        threads=True,
        progress=False,
        auto_adjust=False,
    )
    results = []
    for ticker in tickers:
        try:
            if isinstance(data.columns, pd.MultiIndex):
                if ticker not in data.columns.get_level_values(0):
                    continue
                df = data[ticker].dropna(how="all")
            else:
                df = data.dropna(how="all")
            if df.empty:
                continue
            results.extend(scan_dataframe(ticker, df, lookback_days=LOOKBACK_DAYS))
        except Exception:
            continue
    return results


def build_report() -> str:
    tickers = refresh_tickers.load_us_symbols()
    if not tickers:
        return "<b>📐 차트 패턴</b>\n  (종목 리스트 없음, refresh-tickers 먼저 실행 필요)"

    matches = scan_all(tickers)
    matches.sort(key=lambda m: m["date"], reverse=True)

    lines = [f"<b>📐 차트 패턴</b> · 최근 1거래일 확정 ({len(matches)}건)"]
    if not matches:
        lines.append("  해당 없음")
        return "\n".join(lines)

    for m in matches[:MAX_ITEMS]:
        emoji = PATTERN_EMOJI.get(m["pattern"], "•")
        lines.append(
            f"  {emoji} <b>{m['ticker']}</b> {m['pattern']} · ${m['price']:.2f}"
            f" ({m['date'].strftime('%Y-%m-%d')})\n"
            f"     {m['detail']}"
        )
    if len(matches) > MAX_ITEMS:
        lines.append(f"  ...외 {len(matches) - MAX_ITEMS}건")
    return "\n".join(lines)


if __name__ == "__main__":
    # 단독 실행 시 텔레그램으로 차트 패턴만 전송 (테스트용)
    from common import telegram
    telegram.send(build_report())
