"""강남자리/뜬자리(데이짱) 매매기법 스캐너 (헤드리스).
원본 Node.js 앱(index.js의 DaejjangTradingAnalyzer)을 참고하여 Python으로 새로 구현.

점수 체계:
- 수렴(convergence) 30점: 주가가 3개 이상 이동평균선과 8% 이내 편차
- 돌파(breakout)   30점: 60일 횡보 후 8% 돌파 (+ 거래량 1.5배 시 +10점)
- 공간(space)      15점: 15% 이내 상단에 저항선 없음
- 정배열(alignment)15점: MA5>10>20>50
합계 60점↑ BUY / 30점↑ WATCH
"""
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

MA_PERIODS = [5, 10, 20, 50, 120, 200]
CONVERGENCE_THRESHOLD = 0.08
BREAKOUT_PERIOD = 60
BREAKOUT_THRESHOLD = 0.08
SIDEWAYS_THRESHOLD = 0.35
SPACE_UPSIDE = 0.15
RECENT_DAYS = 3


def sma(arr: np.ndarray, period: int) -> np.ndarray:
    if len(arr) < period:
        return np.array([])
    c = np.cumsum(arr, dtype=float)
    c[period:] = c[period:] - c[:-period]
    return c[period - 1:] / period


def get_ohlcv(symbol: str, period: str = "1y") -> pd.DataFrame | None:
    try:
        data = yf.download(symbol, period=period, progress=False, auto_adjust=True, threads=False)
        if data is None or data.empty or len(data) < 210:
            return None
        if isinstance(data.columns, pd.MultiIndex):
            data.columns = [c[0] if isinstance(c, tuple) else c for c in data.columns]
        return data
    except Exception as e:
        print(f"[daejjang] {symbol} 조회 실패: {e}", file=sys.stderr)
        return None


def check_convergence(closes: np.ndarray) -> dict:
    if len(closes) < 200:
        return {"is_converged": False, "first_detected_days_ago": None, "count": 0}

    mas = {p: sma(closes, p) for p in MA_PERIODS}
    check_days = min(30, len(closes) - 200)
    first_day_ago = None

    for day_offset in range(check_days):
        idx = len(closes) - 1 - day_offset
        price = closes[idx]
        conv = 0
        for p, ma in mas.items():
            ma_idx = idx - (len(closes) - len(ma))
            if 0 <= ma_idx < len(ma):
                if abs(price - ma[ma_idx]) / price <= CONVERGENCE_THRESHOLD:
                    conv += 1
        if conv >= 3:
            first_day_ago = day_offset
            break

    # 현재 상태
    current = closes[-1]
    conv_count = 0
    for p, ma in mas.items():
        if len(ma) > 0 and abs(current - ma[-1]) / current <= CONVERGENCE_THRESHOLD:
            conv_count += 1

    return {
        "is_converged": conv_count >= 3,
        "first_detected_days_ago": first_day_ago,
        "count": conv_count,
    }


def check_breakout(closes: np.ndarray, volumes: np.ndarray) -> dict:
    if len(closes) < BREAKOUT_PERIOD + 10:
        return {"is_breakout": False, "first_detected_days_ago": None, "volume_confirmed": False}

    first_day_ago = None
    check_days = min(10, len(closes) - BREAKOUT_PERIOD)

    for day_offset in range(check_days):
        end_idx = len(closes) - 1 - day_offset
        start_idx = end_idx - BREAKOUT_PERIOD + 1
        if start_idx < 0:
            continue
        period_prices = closes[start_idx:end_idx + 1]
        day_price = closes[end_idx]
        # 마지막 5일 제외한 횡보 구간
        sideways = period_prices[:-5]
        if len(sideways) == 0:
            continue
        avg = float(np.mean(sideways))
        if avg == 0:
            continue
        pmin = float(np.min(sideways))
        pmax = float(np.max(sideways))
        range_ratio = (pmax - pmin) / avg
        is_sideways = range_ratio < SIDEWAYS_THRESHOLD
        breakout_ratio = (day_price - pmax) / avg
        is_broke = breakout_ratio > BREAKOUT_THRESHOLD

        vol_ok = True
        if volumes is not None and len(volumes) == len(closes):
            recent_v = volumes[start_idx:end_idx]
            if len(recent_v) > 0 and np.mean(recent_v) > 0:
                vol_ok = volumes[end_idx] >= np.mean(recent_v) * 1.5

        if is_sideways and is_broke and vol_ok:
            first_day_ago = day_offset
            break

    # 현재 상태 체크
    recent = closes[-BREAKOUT_PERIOD:]
    sideways = recent[:-5]
    avg = float(np.mean(sideways))
    pmax = float(np.max(sideways))
    range_ratio = (float(np.max(sideways)) - float(np.min(sideways))) / avg if avg else 0
    is_sideways = range_ratio < SIDEWAYS_THRESHOLD
    current = closes[-1]
    breakout_ratio = (current - pmax) / avg if avg else 0
    is_broke = breakout_ratio > BREAKOUT_THRESHOLD

    vol_ok = True
    if volumes is not None and len(volumes) >= BREAKOUT_PERIOD:
        recent_v = volumes[-BREAKOUT_PERIOD:-1]
        if len(recent_v) > 0 and np.mean(recent_v) > 0:
            vol_ok = volumes[-1] >= np.mean(recent_v) * 1.5

    return {
        "is_breakout": is_sideways and is_broke and vol_ok,
        "first_detected_days_ago": first_day_ago,
        "volume_confirmed": vol_ok,
        "sideways_ratio": range_ratio,
        "breakout_ratio": breakout_ratio,
    }


def find_resistance(closes: np.ndarray, lookback: int = 20) -> list[float]:
    levels = []
    for i in range(lookback, len(closes) - lookback):
        window = closes[i - lookback:i + lookback + 1]
        if closes[i] >= window.max():
            levels.append(float(closes[i]))
    return levels


def analyze_space(closes: np.ndarray) -> dict:
    if len(closes) < 50:
        return {"has_space": False, "first_detected_days_ago": None}

    first_day_ago = None
    check_days = min(15, len(closes) - 50)
    for day_offset in range(check_days):
        day_idx = len(closes) - 1 - day_offset
        day_price = closes[day_idx]
        hist = closes[:day_idx + 1]
        levels = find_resistance(hist)
        blocks = [lv for lv in levels if day_price < lv < day_price * (1 + SPACE_UPSIDE)]
        if not blocks:
            first_day_ago = day_offset
            break

    current = closes[-1]
    levels = find_resistance(closes)
    blocks = [lv for lv in levels if current < lv < current * (1 + SPACE_UPSIDE)]
    return {"has_space": len(blocks) == 0, "first_detected_days_ago": first_day_ago}


def check_alignment(closes: np.ndarray) -> bool:
    if len(closes) < 50:
        return False
    ma5, ma10, ma20, ma50 = sma(closes, 5), sma(closes, 10), sma(closes, 20), sma(closes, 50)
    return bool(ma5[-1] > ma10[-1] > ma20[-1] > ma50[-1])


def analyze(symbol: str) -> dict | None:
    data = get_ohlcv(symbol)
    if data is None:
        return None
    closes = data["Close"].to_numpy()
    volumes = data["Volume"].to_numpy()

    conv = check_convergence(closes)
    brk = check_breakout(closes, volumes)
    sp = analyze_space(closes)
    aligned = check_alignment(closes)

    # 전체 점수
    total = 0
    total += 30 if conv["is_converged"] else 0
    total += 30 if brk["is_breakout"] else 0
    total += 15 if sp["has_space"] else 0
    total += 15 if aligned else 0
    total += 10 if (brk["is_breakout"] and brk["volume_confirmed"]) else 0

    # 최근 3일 이내 신호 여부
    def recent(days_ago):
        return days_ago is not None and days_ago <= RECENT_DAYS

    recent_score = 0
    recent_score += 30 if (conv["is_converged"] and recent(conv["first_detected_days_ago"])) else 0
    recent_score += 30 if (brk["is_breakout"] and recent(brk["first_detected_days_ago"])) else 0
    recent_score += 15 if (sp["has_space"] and recent(sp["first_detected_days_ago"])) else 0
    recent_score += 15 if aligned else 0
    recent_score += 10 if (recent_score >= 30 and brk["volume_confirmed"]) else 0

    def rec(score):
        return "BUY" if score >= 60 else ("WATCH" if score >= 30 else "PASS")

    last_close = float(closes[-1])
    prev_close = float(closes[-2]) if len(closes) >= 2 else last_close
    change_pct = (last_close - prev_close) / prev_close * 100 if prev_close else 0

    return {
        "symbol": symbol,
        "total_score": total,
        "recent_score": recent_score,
        "total_rec": rec(total),
        "recent_rec": rec(recent_score),
        "signals": {
            "convergence": conv["is_converged"],
            "breakout": brk["is_breakout"],
            "space": sp["has_space"],
            "alignment": aligned,
            "volume": brk["volume_confirmed"],
        },
        "price": last_close,
        "change_pct": change_pct,
    }


def scan_many(symbols: list[str], max_time_sec: int = 360) -> list[dict]:
    results = []
    start = datetime.now()
    for i, sym in enumerate(symbols):
        elapsed = (datetime.now() - start).total_seconds()
        if elapsed > max_time_sec:
            print(f"[daejjang] 타임아웃으로 {i}/{len(symbols)}에서 중단", file=sys.stderr)
            break
        r = analyze(sym)
        if r:
            results.append(r)
    return results


def format_report(title: str, results: list[dict], limit: int = 10) -> str:
    """BUY/WATCH 종목만 추려 점수순 정렬."""
    buy = [r for r in results if r["recent_rec"] == "BUY"]
    watch = [r for r in results if r["recent_rec"] == "WATCH"]
    buy.sort(key=lambda r: r["recent_score"], reverse=True)
    watch.sort(key=lambda r: r["recent_score"], reverse=True)

    lines = [f"<b>🏯 {title}</b> (BUY {len(buy)} · WATCH {len(watch)})"]
    if buy:
        lines.append("  <b>[BUY · 최근 3일 이내]</b>")
        for r in buy[:limit]:
            sig_emojis = _sig_emojis(r["signals"])
            lines.append(
                f"  🟢 <b>{r['symbol']}</b> {r['recent_score']}점 {sig_emojis} · {r['change_pct']:+.1f}%"
            )
    if watch:
        shown = watch[:max(0, limit - len(buy))]
        if shown:
            lines.append("  <b>[WATCH]</b>")
            for r in shown:
                sig_emojis = _sig_emojis(r["signals"])
                lines.append(
                    f"  🟡 <b>{r['symbol']}</b> {r['recent_score']}점 {sig_emojis} · {r['change_pct']:+.1f}%"
                )
    if not buy and not watch:
        lines.append("  (BUY/WATCH 신호 없음)")
    return "\n".join(lines)


def _sig_emojis(sig: dict) -> str:
    s = ""
    s += "🎯" if sig["convergence"] else ""
    s += "🚀" if sig["breakout"] else ""
    s += "🌌" if sig["space"] else ""
    s += "📈" if sig["alignment"] else ""
    s += "📊" if sig["volume"] else ""
    return s or "—"


if __name__ == "__main__":
    test_symbols = sys.argv[1:] or ["AAPL", "NVDA", "TSLA", "005930.KS"]
    results = scan_many(test_symbols)
    print(format_report("강남자리·뜬자리", results))
