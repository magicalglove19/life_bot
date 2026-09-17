# -*- coding: utf-8 -*-
"""신호 선별·랭킹 (2026-09 백테스트 기반).

■ 왜 이 모듈이 생겼나
예전 아침 브리핑은 변곡점·강남자리·차트패턴 결과를 그냥 나열해서 하루 60~75종목이
왔고, 점수가 없어 무엇부터 볼지 알 수 없었다. 미국 시총 상위 395종목 × 3개 시장
구간(2022 하락장 / 2023 회복장 / 2025~26)으로 백테스트한 결과:

  · 패턴 신호 자체는 엣지가 없었다. "아무 종목이나 아무 날 매수"와 승률 차이 +0.2%p.
  · 승률을 올리는 요소는 거의 없었고, 수익은 소수의 대박(상위 5%)에서 나왔다.
  · 대박 확률을 실제로 가르는 건 아래 두 가지였고, 세 구간 모두 같은 방향이었다.
      - 6개월 상대강도(RS): +40%p 이상 구간의 대박확률 17.1% / 7.8% / 11.5%
      - ATR 변동성: 2% 미만은 대박확률 0.9% / 0.6% / 0.0% (꼬리가 아예 없음)
  · 임계값은 넓은 고원이다(RS 30~60, ATR 3~5% 모두 작동) → 특정 값 과적합이 아니다.

최종 규칙 성과 (20거래일 보유, SPY 대비 초과수익, 기준선 차감 후):
    2025~26 +5.09%p / 2022 +4.53%p / 2023 +5.55%p — 세 구간 모두 유의.
    상위 5%를 제외해도 +2.56 / +3.77 / +1.99%p로 엣지가 남는다.

■ 한국은 규칙이 다르다 (2026-09 별도 백테스트, 코스피 296종목 × 3구간)
미국 관문을 한국에 그대로 적용하면 2022 하락장에서 **기준선 대비 -1.86%p**로
오히려 해로웠다. 원인은 RS다. 한국에서 RS 관문은 하락장에 -3.3 ~ -6.4%p로
일관되게 마이너스였다(미국과 정반대). 세 구간 모두 플러스였던 유일한 조합은
ATR≥6% + 거래량≥1.5x 였고, 그마저 하락장 기여는 +0.24%p로 사실상 0이다.

따라서 한국은:
  · RS 관문 제외 (해로움), 120MA 관문 제외 (하락장 -1.55%p)
  · ATR≥6% + 거래량≥1.5x 만 적용, 거래대금은 체결 가능성용 하한
  · 근거가 약하다는 것을 리포트에 명시한다
"""
import html
import sys

import numpy as np
import pandas as pd

# ── 미국: 백테스트로 확정 (모두 '넓은 고원'의 중앙값) ────────────────────
RS_MIN = 40.0          # 6개월 상대강도, 벤치마크 대비 %p
ATR_MIN_PCT = 3.5      # ATR(14) / 종가, %
VOL_RATIO_MIN = 1.2    # 당일 거래량 / 20일 평균
DOLLAR_VOL_MIN_US = 1e8        # $100M — 하락장에서 +1.7%p 기여
USE_RS_GATE_US = True
USE_MA120_GATE_US = True

# ── 한국: 별도 백테스트 결과 다른 규칙 (근거 약함) ──────────────────────
RS_MIN_KR = None               # RS 관문 없음 — 하락장에 해로웠다
ATR_MIN_PCT_KR = 6.0           # 한국은 6% 이상에서만 일관되게 플러스
VOL_RATIO_MIN_KR = 1.5
DOLLAR_VOL_MIN_KR = 1e9        # 10억원 — 엣지가 아니라 체결 가능성 하한
USE_MA120_GATE_KR = False      # 하락장 -1.55%p

RS_PERIOD = 126        # 6개월
HOLD_DAYS = 20         # 백테스트 보유 기간 — 리포트에 명시
LOOKBACK_BARS = 3      # 최근 3거래일 내 발생 신호까지 인정

# ── 점수 (0~100) ────────────────────────────────────────────────────────
# 관문은 통과/탈락만 알려줘서 순서를 정할 수 없다. 관문에 쓰인 지표를 그대로
# 연속 점수로 바꿔 합산한다. 배점은 백테스트에서 기여가 컸던 순서(RS > ATR >
# 거래량 > 120MA·거래대금 > 패턴 수)를 따랐다. 배점 자체는 별도 검증 전이다.
# (항목, 만점, 0점 기준, 만점 기준)
SCORE_RULES = [
    ("rs126",         40, 0.0, 60.0),   # RS 30~60 고원 → 60에서 포화
    ("atr_pct",       25, 2.0, 5.0),    # 2% 미만은 대박 꼬리가 없었다
    ("volume_ratio",  15, 1.0, 2.5),
    ("ma120_gap_pct", 10, 0.0, 15.0),   # 120MA 아래면 0점
    ("dollar_vol_log", 5, 7.3, 8.0),    # log10($): $20M → 0, $100M → 만점
    ("n_pat",          5, 0.0, 3.0),    # 패턴 자체엔 엣지가 없어 가중치 최소
]


def score(m: dict) -> int:
    """관문 지표를 0~100 점수로. 항목별로 0점~만점 기준 사이를 선형 보간."""
    vals = dict(m)
    vals["dollar_vol_log"] = float(np.log10(max(m["dollar_vol"], 1.0)))
    total = 0.0
    for key, pts, lo, hi in SCORE_RULES:
        x = vals.get(key)
        if x is None or np.isnan(x):
            continue
        total += pts * min(max((x - lo) / (hi - lo), 0.0), 1.0)
    return int(round(total))


def _benchmark_return(bench: pd.Series, index: pd.DatetimeIndex) -> float | None:
    b = bench.reindex(index).ffill()
    if len(b) < RS_PERIOD + 1 or pd.isna(b.iloc[-1]) or pd.isna(b.iloc[-1 - RS_PERIOD]):
        return None
    prev = float(b.iloc[-1 - RS_PERIOD])
    if prev <= 0:
        return None
    return (float(b.iloc[-1]) / prev - 1) * 100


def measure(symbol: str, df: pd.DataFrame, bench: pd.Series) -> dict | None:
    """관문 판정에 필요한 지표를 전부 계산. 계산 불가면 None."""
    if df is None or len(df) < RS_PERIOD + 20:
        return None
    c, h, l, v = df["Close"], df["High"], df["Low"], df["Volume"]
    last = float(c.iloc[-1])
    if last <= 0 or float(v.tail(5).sum()) == 0:
        return None

    bench_ret = _benchmark_return(bench, df.index)
    if bench_ret is None:
        return None
    prev126 = float(c.iloc[-1 - RS_PERIOD])
    if prev126 <= 0:
        return None
    rs = (last / prev126 - 1) * 100 - bench_ret

    vol_ma20 = float(v.rolling(20).mean().iloc[-1])
    ma120 = float(c.rolling(120).mean().iloc[-1]) if len(c) >= 120 else np.nan
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr_pct = float(tr.rolling(14).mean().iloc[-1]) / last * 100
    prev_close = float(c.iloc[-2])

    return {
        "symbol": symbol,
        "price": last,
        "change_pct": (last - prev_close) / prev_close * 100 if prev_close > 0 else 0.0,
        "rs126": rs,
        "atr_pct": atr_pct,
        "volume_ratio": float(v.iloc[-1]) / vol_ma20 if vol_ma20 > 0 else 0.0,
        "ma120": ma120,
        "above_ma120": bool(last > ma120) if not np.isnan(ma120) else False,
        "ma120_gap_pct": (last / ma120 - 1) * 100 if ma120 and not np.isnan(ma120) else 0.0,
        "dollar_vol": float((c * v).rolling(20).mean().iloc[-1]),
        "date": df.index[-1].strftime("%Y-%m-%d"),
    }


def gate(m: dict, is_kr: bool) -> tuple[bool, list[str]]:
    """관문 통과 여부와, 떨어졌다면 그 이유. 한국/미국이 서로 다른 규칙을 쓴다."""
    rs_min = RS_MIN_KR if is_kr else RS_MIN
    atr_min = ATR_MIN_PCT_KR if is_kr else ATR_MIN_PCT
    vol_min = VOL_RATIO_MIN_KR if is_kr else VOL_RATIO_MIN
    dv_min = DOLLAR_VOL_MIN_KR if is_kr else DOLLAR_VOL_MIN_US
    use_ma120 = USE_MA120_GATE_KR if is_kr else USE_MA120_GATE_US

    fails = []
    if rs_min is not None and m["rs126"] < rs_min:
        fails.append(f"RS {m['rs126']:+.0f}<{rs_min:.0f}")
    if m["atr_pct"] < atr_min:
        fails.append(f"ATR {m['atr_pct']:.1f}%<{atr_min}%")
    if m["volume_ratio"] < vol_min:
        fails.append(f"거래량 {m['volume_ratio']:.1f}x<{vol_min}x")
    if use_ma120 and not m["above_ma120"]:
        fails.append("120MA 아래")
    if m["dollar_vol"] < dv_min:
        fails.append("거래대금 부족")
    return (not fails), fails


def rank(store: dict[str, pd.DataFrame], bench: pd.Series,
         patterns_by_symbol: dict[str, list[str]], is_kr: bool = False,
         names: dict[str, str] | None = None) -> dict:
    """패턴이 하나라도 잡힌 종목에 점수를 매기고 관문 통과 여부로 나눠 점수순 정렬.

    patterns_by_symbol: {심볼: [패턴명, ...]} — 변곡점 + 차트패턴 통합 결과
    반환: {"passed": [...], "near": [...], "rest": [...]}
          (near = 관문 1개만 탈락, rest = 2개 이상 탈락)
    """
    passed, near, rest = [], [], []
    for sym, pats in patterns_by_symbol.items():
        df = store.get(sym)
        if df is None or not pats:
            continue
        try:
            m = measure(sym, df, bench)
        except Exception as e:
            print(f"[signal_rank] {sym} 지표 실패: {e}", file=sys.stderr)
            continue
        if m is None:
            continue
        m["patterns"] = pats
        m["n_pat"] = len(pats)
        m["name"] = (names or {}).get(sym, "")
        m["score"] = score(m)
        ok, fails = gate(m, is_kr)
        m["fails"] = fails
        if ok:
            passed.append(m)
        elif len(fails) == 1:
            near.append(m)
        else:
            rest.append(m)

    # 점수 순. 동점이면 RS(세 구간 공통으로 대박 확률을 가른 지표).
    key = lambda x: (x["score"], x["rs126"])
    passed.sort(key=key, reverse=True)
    near.sort(key=key, reverse=True)
    rest.sort(key=key, reverse=True)
    return {"passed": passed, "near": near, "rest": rest}


# 2022·2023 일별 재생 백테스트(20거래일, 기준선 대비):
#   매일 15개 채우기  2022 -0.47%p / 2023 +1.01%p  ← 하락장에 40점대 채움 종목이 손해
#   60점 이상만       2022 +1.04%p / 2023 +3.22%p  (하루 3~4개로 저절로 줄어듦)
MIN_SCORE = 60

# ── 낙폭 관리 (같은 일별 재생 백테스트) ─────────────────────────────────
# 손절: -8~-10%·ATR×2는 정상 흔들림에 40~58%가 걸려 평균수익이 오히려 나빠졌다.
#   -15%는 매매당 -0.4~0.5%p 비용으로 최악 5% 거래를 -25% → -16~18%로 줄였다.
STOP_PCT = 15
# 비중: 손절해도 갭으로 -15~20%까지 잃을 수 있어, 종목당 계좌 1% 손실 한도 ≈ 5%.
POSITION_MAX_PCT = 5
# 시장 필터: SPY 50일선 < 200일선이면 신규 매수 중단. 계좌 최대낙폭(20일 굴림 모델)
#   2022 -18.3% → -3.5% (수익 +4.9 → +6.4%), 2023 수익 71.9 → 68.7%, 2021·2024 영향 없음.
#   'SPY > 200일선'은 2022 반등마다 켜졌다 꺼져 오히려 손해였다(-5.4%).
#   하락장 표본은 2022 한 번뿐이고, 2020년 같은 급락엔 늦게 꺼진다.
REGIME_FAST, REGIME_SLOW = 50, 200
REGIME_OFF_WATCH = 3     # 필터가 꺼진 날 관찰용으로만 보여줄 개수


def market_regime(bench: pd.Series) -> dict | None:
    """벤치마크 50일선/200일선 비교. 데이터가 모자라면 None (필터 판단 불가 → 켜진 것으로 취급)."""
    b = bench.dropna()
    if len(b) < REGIME_SLOW:
        return None
    fast = float(b.rolling(REGIME_FAST).mean().iloc[-1])
    slow = float(b.rolling(REGIME_SLOW).mean().iloc[-1])
    return {"on": fast > slow, "price": float(b.iloc[-1]), "ma_fast": fast, "ma_slow": slow}


def top_n(result: dict, n: int, min_score: int = MIN_SCORE) -> list[dict]:
    """관문 통과 종목을 먼저 넣고 남는 자리를 나머지 점수순으로 채운 뒤,
    min_score 미만은 버린다. 표시 순서는 점수순 (동점이면 RS)."""
    key = lambda x: (x["score"], x["rs126"])
    passed = result["passed"][:n]
    others = sorted(result["near"] + result.get("rest", []), key=key, reverse=True)
    picks = passed + others[:n - len(passed)]
    return sorted([m for m in picks if m["score"] >= min_score], key=key, reverse=True)


def _fmt_price(m: dict, is_kr: bool) -> str:
    return f"{m['price']:,.0f}원" if is_kr else f"${m['price']:,.2f}"


PATTERN_SHORT = {
    "컵위드핸들": "컵핸들",
    "더블 바텀": "쌍바닥",
    "갭 상승": "갭업",
}


def format_report(title: str, result: dict, is_kr: bool = False,
                  limit: int = 15, tags: dict[str, str] | None = None,
                  regime: dict | None = None, min_score: int = MIN_SCORE,
                  subtitle: str | None = None) -> str:
    """종목당 2줄. 1줄: 순위·티커·점수·가격·손절가  2줄: 핵심 지표·패턴·미달 사유.

    ✅ 관문 전부 통과 / ⚠️ 1개 미달 / ▫️ 2개 이상 미달. MIN_SCORE 미만은 싣지 않는다.
    regime["on"]이 False면 매수 후보 대신 관찰용 상위 REGIME_OFF_WATCH개만 싣는다.
    """
    passed, near = result["passed"], result["near"]
    total = len(passed) + len(near) + len(result.get("rest", []))
    picks = top_n(result, limit, min_score)
    off = regime is not None and not regime["on"]

    if off:
        picks = picks[:REGIME_OFF_WATCH]
        lines = [f"<b>━━━ 🔴 시장 하락추세 — 신규 매수 쉬는 구간 ━━━</b>",
                 f"<i>SPY {REGIME_FAST}일선 {regime['ma_fast']:,.1f} &lt; {REGIME_SLOW}일선 "
                 f"{regime['ma_slow']:,.1f}. 다시 위로 올라서면 매수 후보를 보냅니다.</i>",
                 f"<i>관찰용 상위 {len(picks)}개 (패턴 {total}종목 중)</i>"]
    else:
        lines = [f"<b>{title}</b>",
                 subtitle or f"<i>패턴 {total}종목 중 {min_score}점 이상 {len(picks)}개 · ✅통과 {len(passed)}</i>"]

    if not picks:
        lines.append(f"  <i>{min_score}점 이상 없음 — 쉬는 날</i>" if not subtitle
                     else "  <i>해당 종목 없음 — 쉬는 날</i>")
    for i, m in enumerate(picks, 1):
        label = html.escape(m["symbol"])
        if is_kr and m.get("name"):
            label += f" {html.escape(m['name'])}"
        mark = "👀" if off else "✅" if not m["fails"] else "⚠️" if len(m["fails"]) == 1 else "▫️"
        stop = m["price"] * (1 - STOP_PCT / 100)
        lines.append(
            f"{i}. {mark} <b>{label}</b> <b>{m['score']}점</b> "
            f"{_fmt_price(m, is_kr)} ({m['change_pct']:+.1f}%) · 손절 {_fmt_price(dict(m, price=stop), is_kr)}"
        )
        pats = html.escape("+".join(PATTERN_SHORT.get(p, p) for p in m["patterns"]))
        detail = (f"    RS{m['rs126']:+.0f} ATR{m['atr_pct']:.1f}% "
                  f"거래량{m['volume_ratio']:.1f}x · {pats}")
        # 미달 사유에는 "거래량 1.0x<1.2x" 처럼 < 가 들어간다. 그대로 보내면
        # 텔레그램 HTML 파서가 태그 시작으로 읽고 400으로 거절해 발송 전체가 죽는다.
        if m["fails"]:
            detail += f" · <i>{html.escape(m['fails'][0])}</i>"
        if tags and m["symbol"] in tags:
            detail += f" · <i>{html.escape(tags[m['symbol']])}</i>"
        lines.append(detail)

    return "\n".join(lines)
