"""변곡점 매수법 스캐너 (헤드리스).
원본 GUI 앱(main.py)의 탐지 알고리즘을 참고하여 새로 구현.
- 패턴 1: 거래량 감소 후 평균 이상 거래량 양봉
- 패턴 2: 횡보 후 장대 양봉
morning_digest.py에서 import해서 호출.
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf


def get_history(symbol: str, period: str = "3mo") -> pd.DataFrame | None:
    try:
        data = yf.download(symbol, period=period, progress=False, auto_adjust=True, threads=False)
        if data is None or data.empty or len(data) < 30:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = [c[0] if isinstance(c, tuple) else c for c in data.columns]
        if data["Close"].isna().all() or data["Volume"].isna().all():
            return None
        return data
    except Exception as e:
        print(f"[inflection] {symbol} 조회 실패: {e}", file=sys.stderr)
        return None


def detect_signal(data: pd.DataFrame) -> tuple[bool, str]:
    if len(data) < 20:
        return False, ""

    # 최근 1개월 이내 데이터만 신호로 인정
    last_date = data.index[-1]
    one_month_ago = datetime.now() - timedelta(days=30)
    if last_date.replace(tzinfo=None) < one_month_ago:
        return False, ""

    recent = data.tail(20).copy()
    recent["VolMA"] = recent["Volume"].rolling(window=10).mean()
    recent["IsGreen"] = recent["Close"] > recent["Open"]
    recent["Size"] = (recent["Close"] - recent["Open"]).abs() / recent["Open"].replace(0, pd.NA)

    signals = []
    if _volume_pattern(recent):
        signals.append("거래량증가양봉")
    if _sideways_pattern(recent):
        signals.append("횡보후장대양봉")
    return (len(signals) > 0, ",".join(signals))


def _volume_pattern(data: pd.DataFrame) -> bool:
    if len(data) < 10:
        return False
    last = data.iloc[-1]
    prev4 = data.iloc[-5:-1]
    if not bool(last["IsGreen"]):
        return False
    vol_ma = last["VolMA"]
    if pd.isna(vol_ma) or last["Volume"] <= vol_ma:
        return False
    # 이전 4일 거래량이 계속 감소했는지
    vols = prev4["Volume"].tolist()
    for i in range(len(vols) - 1):
        if vols[i + 1] >= vols[i]:
            return False
    return True


def _sideways_pattern(data: pd.DataFrame) -> bool:
    if len(data) < 15:
        return False
    last = data.iloc[-1]
    sideways = data.iloc[-10:-1]
    size = last["Size"]
    if pd.isna(size) or not bool(last["IsGreen"]) or float(size) < 0.03:
        return False
    high_range = sideways["High"].max() - sideways["Low"].min()
    mean_close = sideways["Close"].mean()
    if mean_close == 0 or pd.isna(mean_close):
        return False
    return (high_range / mean_close) < 0.1


def scan_one(symbol: str) -> dict | None:
    data = get_history(symbol)
    if data is None:
        return None
    if data["Volume"].sum() == 0:
        return None
    has, signal_type = detect_signal(data)
    if not has:
        return None
    last_close = float(data["Close"].iloc[-1])
    prev_close = float(data["Close"].iloc[-2]) if len(data) >= 2 else last_close
    if prev_close == 0:
        return None
    change_pct = (last_close - prev_close) / prev_close * 100
    vol_ma = data["Volume"].rolling(window=10).mean().iloc[-1]
    vol_ratio = float(data["Volume"].iloc[-1] / vol_ma) if vol_ma and not pd.isna(vol_ma) else 1.0
    return {
        "symbol": symbol,
        "price": last_close,
        "change_pct": change_pct,
        "volume_ratio": vol_ratio,
        "signal": signal_type,
        "date": data.index[-1].strftime("%Y-%m-%d"),
    }


def scan_many(symbols: list[str], max_time_sec: int = 360) -> list[dict]:
    """여러 종목 스캔. max_time_sec 초과 시 조기 종료."""
    results = []
    start = datetime.now()
    for i, sym in enumerate(symbols):
        elapsed = (datetime.now() - start).total_seconds()
        if elapsed > max_time_sec:
            print(f"[inflection] 타임아웃으로 {i}/{len(symbols)}에서 중단", file=sys.stderr)
            break
        r = scan_one(sym)
        if r:
            results.append(r)
    results.sort(key=lambda x: x["change_pct"], reverse=True)
    return results


def format_report(title: str, items: list[dict], limit: int = 10) -> str:
    lines = [f"<b>🎯 {title}</b> ({len(items)}개 발견)"]
    if not items:
        lines.append("  (신호 종목 없음)")
        return "\n".join(lines)
    for r in items[:limit]:
        lines.append(
            f"  • <b>{r['symbol']}</b> {r['change_pct']:+.1f}% · 거래량 {r['volume_ratio']:.1f}x · {r['signal']}"
        )
    if len(items) > limit:
        lines.append(f"  <i>...외 {len(items) - limit}개</i>")
    return "\n".join(lines)


if __name__ == "__main__":
    # 단독 테스트: python inflection_scanner.py AAPL MSFT NVDA
    test_symbols = sys.argv[1:] or ["AAPL", "NVDA", "TSLA"]
    results = scan_many(test_symbols)
    print(format_report("변곡점 매수 신호", results))
