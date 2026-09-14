# -*- coding: utf-8 -*-
"""변곡점 매수법 스캐너 (헤드리스).
- 패턴 1: 거래량 4일 연속 감소 후 10일 평균 이상 거래량의 양봉
- 패턴 2: 횡보 구간을 장대양봉으로 고점 돌파

■ 2026-09 백테스트 반영 사항
1) 조회 기간 3mo → market_data(2y). 예전에는 3개월치만 받아서 120일선·ATR을
   계산할 수 없었고, 그래서 하락추세 종목을 걸러내지 못했다.
2) 횡보돌파의 '실제 돌파' 조건을 필수로. 예전 로직은 4개 조건 중 3개만 채우면
   통과라서, 돌파가 없어도 '횡보 + 거래량 감소'만으로 신호가 났다.
   1년 백테스트에서 이 패턴 신호의 57%가 그런 가짜였다.
3) 종목당 개별 yfinance 호출 제거 → market_data.fetch()가 받아온 공용 데이터 사용.
   타임아웃으로 리스트 앞부분만 스캔되고 끊기던 문제가 사라진다.
"""
import sys

import numpy as np
import pandas as pd

PAT_VOLUME = "거래량폭증양봉"
PAT_SIDEWAYS = "횡보돌파"


def _metrics(df: pd.DataFrame) -> dict | None:
    """마지막 봉 기준 공용 지표."""
    if df is None or len(df) < 130:
        return None
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    last = float(c.iloc[-1])
    prev = float(c.iloc[-2])
    if last <= 0 or prev <= 0:
        return None
    if float(v.tail(5).sum()) == 0:
        return None

    vol_ma20 = float(v.rolling(20).mean().iloc[-1])
    ma120 = float(c.rolling(120).mean().iloc[-1])
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_pct = float(tr.rolling(14).mean().iloc[-1]) / last

    return {
        "price": last,
        "change_pct": (last - prev) / prev * 100,
        "volume_ratio": float(v.iloc[-1]) / vol_ma20 if vol_ma20 > 0 else 0.0,
        "ma120": ma120,
        "above_ma120": last > ma120,
        "ma120_gap_pct": (last / ma120 - 1) * 100 if ma120 > 0 else 0.0,
        "atr_pct": atr_pct * 100,
        "dollar_vol": float((c * v).rolling(20).mean().iloc[-1]),
        "date": df.index[-1].strftime("%Y-%m-%d"),
    }


def _volume_pattern_at(df: pd.DataFrame, i: int) -> bool:
    """i번째 봉에서 패턴1 성립 여부."""
    if i < 10:
        return False
    o, c, v = df["Open"].values, df["Close"].values, df["Volume"].values
    if not (c[i] > o[i]):
        return False
    vma10 = float(pd.Series(v[:i + 1]).rolling(10).mean().iloc[-1])
    if np.isnan(vma10) or vma10 <= 0 or v[i] <= vma10:
        return False
    p = v[i - 4:i]
    if len(p) != 4:
        return False
    return bool(p[1] < p[0] and p[2] < p[1] and p[3] < p[2])


def _sideways_pattern_at(df: pd.DataFrame, i: int) -> bool:
    """i번째 봉에서 패턴2 성립 여부.

    필수: 장대양봉(body>2.5%)으로 직전 10봉 고점 돌파.
    보조(점수): 횡보 CV<3%, 횡보 중 거래량 감소, 돌파 거래량 1.3배.
    필수 + 보조 2개 이상이어야 신호.
    """
    if i < 25:
        return False
    o, h, c, v = (df[x].values for x in ["Open", "High", "Close", "Volume"])
    if o[i] <= 0:
        return False

    sw_c, sw_h, sw_v = c[i - 10:i], h[i - 10:i], v[i - 10:i]
    mean_c, mean_v = sw_c.mean(), sw_v.mean()
    if mean_c <= 0 or mean_v <= 0:
        return False

    body = (c[i] - o[i]) / o[i]
    # 필수 조건: 실제 돌파가 있어야 '횡보 돌파'다
    if not (c[i] > o[i] and body > 0.025 and c[i] > sw_h.max()):
        return False

    vma20 = float(pd.Series(v[:i + 1]).rolling(20).mean().iloc[-1])
    score = 1
    if sw_c.std(ddof=1) / mean_c < 0.03:
        score += 1
    if not np.isnan(vma20) and mean_v < vma20:
        score += 1
    if v[i] > mean_v * 1.3:
        score += 1
    return score >= 3


def detect(df: pd.DataFrame, lookback_bars: int = 3) -> list[str]:
    """최근 lookback_bars 봉 안에 성립한 변곡점 패턴 이름 목록."""
    if df is None or len(df) < 40:
        return []
    found = []
    n = len(df)
    for back in range(lookback_bars):
        i = n - 1 - back
        if i < 25:
            break
        if PAT_VOLUME not in found and _volume_pattern_at(df, i):
            found.append(PAT_VOLUME)
        if PAT_SIDEWAYS not in found and _sideways_pattern_at(df, i):
            found.append(PAT_SIDEWAYS)
    return found


def scan_df(symbol: str, df: pd.DataFrame, lookback_bars: int = 3) -> dict | None:
    """공용 데이터로 한 종목 스캔."""
    pats = detect(df, lookback_bars)
    if not pats:
        return None
    m = _metrics(df)
    if m is None:
        return None
    m.update({"symbol": symbol, "patterns": pats, "signal": ",".join(pats)})
    return m


def scan_store(store: dict[str, pd.DataFrame], lookback_bars: int = 3) -> list[dict]:
    out = []
    for sym, df in store.items():
        try:
            r = scan_df(sym, df, lookback_bars)
            if r:
                out.append(r)
        except Exception as e:
            print(f"[inflection] {sym} 실패: {e}", file=sys.stderr)
    out.sort(key=lambda x: x["change_pct"], reverse=True)
    return out


def format_report(title: str, items: list[dict], limit: int = 10) -> str:
    lines = [f"<b>🎯 {title}</b> ({len(items)}개 발견)"]
    if not items:
        lines.append("  (신호 종목 없음)")
        return "\n".join(lines)
    for r in items[:limit]:
        lines.append(
            f"  • <b>{r['symbol']}</b> {r['change_pct']:+.1f}% · "
            f"거래량 {r['volume_ratio']:.1f}x · {r['signal']}"
        )
    if len(items) > limit:
        lines.append(f"  <i>...외 {len(items) - limit}개</i>")
    return "\n".join(lines)


if __name__ == "__main__":
    import market_data
    syms = sys.argv[1:] or ["AAPL", "NVDA", "TSLA"]
    store = market_data.fetch(syms)
    print(format_report("변곡점 매수 신호", scan_store(store)))
