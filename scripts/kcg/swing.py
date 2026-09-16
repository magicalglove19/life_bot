"""일봉 스윙 — 신고가 주도주의 눌림목을 사서 5일선이 살아 있는 동안 들고 간다.

  타점 1  수급 음봉 — 조정 중 거래량이 급감하며 5일선 부근에서 나온 짧은 캔들 (1차 분할)
  타점 2  20일선 눌림 후 5일선 돌파 안착 (메인 스윙 타점)

자격은 둘 다 같다 — 대량 거래를 동반한 60일 신고가, 우상향 20일선, 급락 없는 눌림.
종가배팅(closing.py)이 하루짜리라면 이쪽은 추세를 들고 가는 매매다.
손절은 매수가 대비 6% 또는 20일선 이탈 중 먼저 닿는 쪽, 청산은 5일선 이탈.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config, SwingConfig
from .indicators import body_pct, gap_pct, is_bull, range_pct
from .patterns import Check

ENTRY_LABEL = {1: "수급 음봉 (5일선 부근 거래량 급감)", 2: "20일선 눌림 후 5일선 돌파"}


@dataclass
class SwingPick:
    code: str
    name: str
    market: str = ""
    entry: int = 0                     # 1 / 2
    matched: bool = False
    score: float = 0.0
    price: float = np.nan
    chg: float = np.nan
    value: float = np.nan              # 오늘 거래대금
    peak_date: str = ""                # 신고가 날짜
    peak_high: float = np.nan
    peak_vol_mult: float = np.nan      # 신고가 당시 거래량 배수
    rest_bars: int = 0                 # 신고가 이후 조정 봉 수
    depth: float = np.nan              # 신고가 고점 대비 조정 저점 하락률 %
    stop_price: float = np.nan         # 손절선 (6% / 20일선 중 높은 쪽)
    stop_label: str = ""
    stop_pct: float = np.nan
    trail_price: float = np.nan        # 5일선 — 이탈하면 매도
    weekly_ok: bool = False            # 주봉 역사적 고점 돌파 + 거래량 동반
    checks: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return ENTRY_LABEL.get(self.entry, "")

    @property
    def note(self) -> str:
        return "; ".join(f"{c.name} {c.detail}" for c in self.checks if not c.ok)


def weekly_breakout(df: pd.DataFrame) -> bool:
    """주봉 검증 — 최근 주봉이 역사적 고점권이고 거래량이 실렸는가."""
    wk = df.resample("W").agg({"High": "max", "Low": "min", "Close": "last", "Volume": "sum"})
    wk = wk.dropna()
    if len(wk) < 12:
        return False
    recent = wk.iloc[-4:]
    hist_high = float(wk["High"].iloc[:-4].max()) if len(wk) > 4 else np.nan
    vol_avg = float(wk["Volume"].iloc[-16:-4].mean())
    if not np.isfinite(hist_high) or vol_avg <= 0:
        return False
    return bool(float(recent["High"].max()) >= hist_high
                and float(recent["Volume"].max()) >= vol_avg * 2.0)


def _peak(df: pd.DataFrame, cfg: SwingConfig) -> int | None:
    """최근 구간의 60일 신고가 봉. 상승이 며칠에 걸쳐 나오면 거래량 배수가 희석되므로,
    고점을 먼저 잡고 그 **주변(±2봉)** 에서 대량 거래가 있었는지로 확인한다."""
    n = len(df)
    high = df["High"].astype(float)
    roll = high.rolling(cfg.high_lookback, min_periods=cfg.high_lookback // 2).max()
    vol20 = df["vol20"].astype(float)
    lo_i = max(cfg.high_lookback // 2, n - 1 - cfg.peak_within)
    hi_i = n - 1 - cfg.peak_min_rest
    cands = [i for i in range(lo_i, hi_i + 1) if high.iloc[i] >= roll.iloc[i]]
    if not cands:
        return None
    peak_i = max(cands, key=lambda i: high.iloc[i])
    near = range(max(0, peak_i - 2), min(n, peak_i + 3))
    mult = max(
        (float(df["Volume"].iloc[j]) / float(vol20.iloc[j])
         for j in near if np.isfinite(vol20.iloc[j]) and vol20.iloc[j] > 0),
        default=0.0,
    )
    return peak_i if mult >= cfg.peak_vol_mult else None


def _base_checks(df: pd.DataFrame, p: SwingPick, peak_i: int, cfg: SwingConfig) -> list[Check]:
    """두 타점이 공유하는 자격 조건 — 신고가 · 정배열 · 눌림."""
    n = len(df)
    last = df.iloc[-1]
    close = float(last["Close"])
    peak = float(df["High"].iloc[peak_i])
    rest = df.iloc[peak_i + 1:]                       # 신고가 다음 봉 ~ 오늘
    rise = df.iloc[max(0, peak_i - 10):peak_i + 1]    # 신고가까지 올라온 구간

    depth = (1.0 - float(rest["Low"].min()) / peak) * 100.0
    ma20 = df["ma20"].astype(float)
    slope_ok = bool(ma20.iloc[-1] > ma20.iloc[-1 - cfg.ma_slope_bars])
    floor = (1.0 - cfg.ma20_tolerance / 100.0)
    held20 = bool((rest["Close"].astype(float) >= ma20.iloc[peak_i + 1:] * floor).all())
    # 오늘 봉은 빼고 센다 — 타점 2는 오늘 거래량이 늘어야 하는데, 그걸 조정 구간
    # 평균에 넣으면 스스로 "거래량 급감"을 깨뜨린다.
    rest_past = rest.iloc[:-1] if len(rest) > 1 else rest
    rise_vol = float(rise["Volume"].mean())
    rest_vol = float(rest_past["Volume"].mean()) / rise_vol if rise_vol > 0 else np.nan
    value20 = float((df["Close"] * df["Volume"]).iloc[-20:].mean())

    p.peak_date = str(df.index[peak_i].date())
    p.peak_high = peak
    near = range(max(0, peak_i - 2), min(n, peak_i + 3))
    p.peak_vol_mult = max(float(df["Volume"].iloc[j]) / float(df["vol20"].iloc[j])
                          for j in near if float(df["vol20"].iloc[j]) > 0)
    p.rest_bars = n - 1 - peak_i
    p.depth = depth
    p.weekly_ok = weekly_breakout(df)

    return [
        Check("신고가 주도주", True,
              f"{p.peak_date} 고가 {peak:,.0f}원 ({cfg.high_lookback}일 신고가 · "
              f"거래량 {p.peak_vol_mult:.1f}배) → {p.rest_bars}봉 경과"),
        Check("거래대금", value20 >= cfg.min_avg_value,
              f"20일 평균 {value20 / 1e8:,.0f}억 (기준 {cfg.min_avg_value / 1e8:,.0f}억)"),
        Check("20일선 우상향", slope_ok,
              f"20일선 {ma20.iloc[-1 - cfg.ma_slope_bars]:,.0f} → {ma20.iloc[-1]:,.0f} "
              f"({cfg.ma_slope_bars}봉)"),
        Check("20일선 사수", held20,
              f"조정 내내 종가가 20일선 위 (허용 -{cfg.ma20_tolerance:.0f}%)"
              if held20 else "조정 중 종가가 20일선을 이탈한 적 있음"),
        Check("급락 없는 눌림", depth <= cfg.max_depth,
              f"고점 대비 최대 -{depth:.1f}% (기준 -{cfg.max_depth:.0f}% 이내)"),
        Check("거래량 급감", np.isfinite(rest_vol) and rest_vol <= cfg.rest_vol_ratio,
              f"조정 구간 거래량이 상승 구간의 {rest_vol * 100:.0f}% "
              f"(기준 {cfg.rest_vol_ratio * 100:.0f}% 이하)"),
        Check("눌림 저점 > 전고점", float(rest["Low"].min()) >= float(df["High"].iloc[:peak_i].max()
                                                                  if peak_i > 0 else 0) * 0.98,
              f"눌림 저점 {float(rest['Low'].min()):,.0f} · "
              f"직전 고점 {float(df['High'].iloc[:peak_i].max()):,.0f}", critical=False),
        Check("주봉 검증", p.weekly_ok,
              "주봉 역사적 고점 돌파 + 거래량 동반" if p.weekly_ok else "주봉 고점 돌파 미확인",
              critical=False),
    ]


def _finish(p: SwingPick, df: pd.DataFrame, cfg: SwingConfig, weights: dict) -> SwingPick:
    p.matched = all(c.ok for c in p.checks if c.critical)
    got = sum(weights.get(c.name, 1.0) for c in p.checks if c.ok)
    total = sum(weights.get(c.name, 1.0) for c in p.checks)
    p.score = round(got / total * 100.0, 1) if total > 0 else 0.0

    last = df.iloc[-1]
    close = float(last["Close"])
    ma20 = float(last["ma20"])
    pct_stop = close * (1.0 - cfg.stop_pct / 100.0)
    # 6% 손절선과 20일선 중 먼저 닿는 쪽(= 높은 쪽)이 실제 손절선이다
    if ma20 > pct_stop:
        p.stop_price, p.stop_label = ma20, "20일선"
    else:
        p.stop_price, p.stop_label = pct_stop, f"{cfg.stop_pct:.0f}% 손절"
    p.stop_pct = (p.stop_price / close - 1.0) * 100.0
    p.trail_price = float(last["ma5"])
    return p


W_COMMON = {"신고가 주도주": 2, "거래대금": 1, "20일선 우상향": 2, "20일선 사수": 2,
            "급락 없는 눌림": 1, "거래량 급감": 2, "눌림 저점 > 전고점": 1, "주봉 검증": 1}
W1 = {**W_COMMON, "5일선 부근": 2, "짧은 캔들": 1, "거래량 급감 (당일)": 2}
W2 = {**W_COMMON, "20일선 지지 확인": 2, "5일선 돌파 안착": 3, "거래량 동반": 1, "추격 아님": 1}


def _pick(code: str, info: dict, df: pd.DataFrame, entry: int) -> SwingPick:
    last = df.iloc[-1]
    close = float(last["Close"])
    prev = float(df["Close"].iloc[-2])
    return SwingPick(
        code=code, name=info.get("name", code), market=info.get("market", ""), entry=entry,
        price=close, chg=(close / prev - 1.0) * 100.0 if prev > 0 else np.nan,
        value=close * float(last["Volume"]),
    )


def detect_entry1(df: pd.DataFrame, code: str, info: dict, cfg: SwingConfig) -> SwingPick:
    """타점 1 — 거래량 급감 + 5일선 부근 짧은 캔들."""
    p = _pick(code, info, df, 1)
    peak_i = _peak(df, cfg)
    if peak_i is None:
        p.checks = [Check("신고가 주도주", False,
                          f"최근 {cfg.peak_within}봉 안에 대량 거래 {cfg.high_lookback}일 신고가 없음")]
        return _finish(p, df, cfg, W1)

    last = df.iloc[-1]
    close = float(last["Close"])
    ma5 = float(last["ma5"])
    g5 = gap_pct(close, ma5)
    vol_ratio = float(last["Volume"]) / float(last["vol20"]) if float(last["vol20"]) > 0 else np.nan

    p.checks = _base_checks(df, p, peak_i, cfg) + [
        Check("5일선 부근", abs(g5) <= cfg.t1_ma5_gap,
              f"5일선 이격 {g5:+.1f}% (기준 ±{cfg.t1_ma5_gap:.0f}%)"),
        Check("짧은 캔들", abs(body_pct(last)) <= cfg.t1_max_body
              and range_pct(last) <= cfg.t1_max_range,
              f"몸통 {body_pct(last):+.1f}% · 길이 {range_pct(last):.1f}% "
              f"(기준 ±{cfg.t1_max_body:.0f}% · {cfg.t1_max_range:.0f}%)"),
        Check("거래량 급감 (당일)", np.isfinite(vol_ratio) and vol_ratio <= cfg.t1_vol_ratio,
              f"20일 평균의 {vol_ratio * 100:.0f}% (기준 {cfg.t1_vol_ratio * 100:.0f}% 이하)"),
    ]
    return _finish(p, df, cfg, W1)


def detect_entry2(df: pd.DataFrame, code: str, info: dict, cfg: SwingConfig) -> SwingPick:
    """타점 2 — 20일선 눌림을 확인하고 5일선을 돌파해 안착."""
    p = _pick(code, info, df, 2)
    peak_i = _peak(df, cfg)
    if peak_i is None:
        p.checks = [Check("신고가 주도주", False,
                          f"최근 {cfg.peak_within}봉 안에 대량 거래 {cfg.high_lookback}일 신고가 없음")]
        return _finish(p, df, cfg, W2)

    last = df.iloc[-1]
    close = float(last["Close"])
    ma5, ma20 = float(last["ma5"]), float(last["ma20"])
    look = df.iloc[-cfg.t2_touch_within - 1:-1]
    touched = bool((look["Low"].astype(float)
                    <= look["ma20"].astype(float) * (1 + cfg.t2_touch_tol / 100.0)).any())
    was_below = float(df["Close"].iloc[-2]) <= float(df["ma5"].iloc[-2])
    vol_ratio = float(last["Volume"]) / float(last["vol20"]) if float(last["vol20"]) > 0 else np.nan
    g5 = gap_pct(close, ma5)

    p.checks = _base_checks(df, p, peak_i, cfg) + [
        Check("20일선 지지 확인", touched,
              f"최근 {cfg.t2_touch_within}봉 안에 저가가 20일선 +{cfg.t2_touch_tol:.0f}% 안까지 눌림"
              if touched else f"최근 {cfg.t2_touch_within}봉 안에 20일선 지지 없음"),
        Check("5일선 돌파 안착", was_below and close > ma5 and is_bull(last),
              f"어제 5일선 {'아래' if was_below else '위'} → 오늘 종가 {close:,.0f} "
              f"vs 5일선 {ma5:,.0f} ({g5:+.1f}%) · {'양봉' if is_bull(last) else '음봉'}"),
        Check("거래량 동반", np.isfinite(vol_ratio) and vol_ratio >= cfg.t2_vol_mult,
              f"20일 평균의 {vol_ratio * 100:.0f}% (기준 {cfg.t2_vol_mult * 100:.0f}% 이상)"),
        Check("추격 아님", g5 <= cfg.t2_max_ma5_gap,
              f"5일선 이격 {g5:+.1f}% (기준 {cfg.t2_max_ma5_gap:.0f}% 이하)", critical=False),
    ]
    return _finish(p, df, cfg, W2)


def scan(frames: dict[str, pd.DataFrame], meta: dict[str, dict], cfg: Config) -> list[SwingPick]:
    """성립한 스윙 타점. 한 종목이 두 타점에 다 걸리면 메인(타점 2)을 쓴다."""
    out: list[SwingPick] = []
    for code, df in frames.items():
        df = df[(df["High"] > 0) & (df["Low"] > 0) & (df["Open"] > 0)]
        if len(df) < 80 or float(df["Close"].iloc[-1]) < cfg.min_price:
            continue
        info = meta.get(code, {})
        hits = [r for r in (detect_entry2(df, code, info, cfg.swing),
                            detect_entry1(df, code, info, cfg.swing)) if r.matched]
        if hits:
            out.append(hits[0] if hits[0].entry == 2 else max(hits, key=lambda r: r.score))
    # 메인 타점(2)을 먼저 보여준다 — 타점 1은 1차 분할용이라 급이 다르다
    out.sort(key=lambda r: (r.entry == 2, r.score, r.value), reverse=True)
    return out


def exit_signals(df: pd.DataFrame, cfg: SwingConfig, avg_price: float | None = None) -> list[str]:
    """보유 중인 스윙 종목의 청산 신호 — 5일선 이탈 / 대량 거래 음봉·윗꼬리 / 손절."""
    out = []
    last, prev = df.iloc[-1], df.iloc[-2]
    close = float(last["Close"])
    ma5, ma20 = float(last["ma5"]), float(last["ma20"])

    if avg_price and close <= avg_price * (1 - cfg.stop_pct / 100.0):
        out.append(f"손절 — 매수가 대비 {(close / avg_price - 1) * 100:+.1f}% "
                   f"(기준 -{cfg.stop_pct:.0f}%)")
    if float(prev["Close"]) >= float(prev["ma20"]) and close < ma20:
        out.append(f"손절 — 20일선 {ma20:,.0f} 하향 이탈")
    if float(prev["Close"]) >= float(prev["ma5"]) and close < ma5:
        out.append(f"매도 — 5일선 {ma5:,.0f} 하향 이탈 (추세 종료)")

    vol_ratio = float(last["Volume"]) / float(last["vol20"]) if float(last["vol20"]) > 0 else 0.0
    rng = float(last["High"]) - float(last["Low"])
    wick = (float(last["High"]) - max(float(last["Open"]), close)) / rng if rng > 0 else 0.0
    if vol_ratio >= cfg.exit_vol_mult and (not is_bull(last) or wick >= cfg.exit_wick_ratio):
        out.append(f"매도 — 대량 거래({vol_ratio:.1f}배) "
                   f"{'음봉' if not is_bull(last) else f'윗꼬리 {wick * 100:.0f}%'} 출현")
    return out


def to_dataframe(picks: list[SwingPick]) -> pd.DataFrame:
    rows = []
    for p in picks:
        rows.append({
            "종목코드": p.code,
            "종목명": p.name,
            "시장": p.market,
            "타점": f"{p.entry} {p.label}",
            "점수": p.score,
            "현재가": round(p.price),
            "등락률": round(p.chg, 2),
            "거래대금(억)": round(p.value / 1e8, 1),
            "신고가일": p.peak_date,
            "신고가": round(p.peak_high),
            "조정봉": p.rest_bars,
            "조정깊이": round(p.depth, 1),
            "손절선": round(p.stop_price),
            "손절기준": p.stop_label,
            "손절폭": round(p.stop_pct, 1),
            "5일선(추세)": round(p.trail_price),
            "주봉": "O" if p.weekly_ok else "-",
            "감점": p.note,
        })
    return pd.DataFrame(rows)
