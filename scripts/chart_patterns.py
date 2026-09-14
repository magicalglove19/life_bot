# -*- coding: utf-8 -*-
"""차트 패턴 스캔 — 컵위드핸들 / 더블바텀 / 갭상승.
로컬 'Chart Pattern Analyzer' 앱(pattern_detectors.py)과 같은 로직.

■ 2026-09 변경
1) V라인 제외. 2022/2023/2025~26 세 구간 모두 SPY 대비 승률 40~44%,
   평균 MAE -13%로 유일하게 확실히 해로운 패턴이었다.
2) 종목별 yfinance 호출 제거 → market_data.fetch()의 공용 데이터 사용.
3) 단독 나열 대신 signal_rank.py의 관문을 거쳐 발송된다.
"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pattern_detectors import scan_dataframe

LOOKBACK_BARS = 3
PATTERN_EMOJI = {
    "컵위드핸들": "☕",
    "더블 바텀": "〰️",
    "갭 상승": "🚀",
}


def detect_store(store: dict[str, pd.DataFrame],
                 lookback_bars: int = LOOKBACK_BARS) -> dict[str, list[str]]:
    """{심볼: [패턴명, ...]} — 최근 lookback_bars 거래일 내 확정된 패턴만."""
    out: dict[str, list[str]] = {}
    for sym, df in store.items():
        if df is None or len(df) < 60:
            continue
        try:
            # 거래일 기준 lookback을 달력일로 환산 (주말·휴일 포함)
            k = min(lookback_bars, len(df) - 1)
            days = max(1, (df.index[-1] - df.index[-1 - k]).days)
            matches = scan_dataframe(sym, df, lookback_days=days)
        except Exception as e:
            print(f"[chart_patterns] {sym} 실패: {e}", file=sys.stderr)
            continue
        names = []
        for m in matches:
            if m["pattern"] not in names:
                names.append(m["pattern"])
        if names:
            out[sym] = names
    return out


def build_report(store: dict[str, pd.DataFrame] | None = None) -> str:
    """단독 실행용 간이 리포트 (관문 미적용)."""
    if store is None:
        import market_data
        import refresh_tickers
        tickers = refresh_tickers.load_us_symbols()
        if not tickers:
            return "<b>📐 차트 패턴</b>\n  (종목 리스트 없음, refresh-tickers 먼저 실행 필요)"
        store = market_data.fetch(tickers)

    found = detect_store(store)
    lines = [f"<b>📐 차트 패턴</b> · 최근 {LOOKBACK_BARS}거래일 ({len(found)}종목)"]
    if not found:
        lines.append("  해당 없음")
        return "\n".join(lines)
    for sym, pats in sorted(found.items()):
        emo = "".join(PATTERN_EMOJI.get(p, "•") for p in pats)
        lines.append(f"  {emo} <b>{sym}</b> {', '.join(pats)}")
    return "\n".join(lines)


if __name__ == "__main__":
    from common import telegram
    telegram.send(build_report())
