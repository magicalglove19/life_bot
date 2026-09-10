"""트리거 이후 일봉 흐름 정리와 '내일 성립 조건' 역산.

정밀 진단의 핵심은 두 가지다.
  1. 트리거 이후 캔들이 실제로 어떻게 흘렀는가 (거래량·이평선·캔들 형태)
  2. 오늘 안 되었다면, 내일 무엇이 충족되면 매수인가

이동평균 조건은 근사가 아니라 정확히 역산된다. 내일 종가를 C라 하면
5일선은 (직전 4일 종가합 + C)/5 이므로, C ≥ 직전4일합/4 이면 5일선 위에서 마감한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config
from .indicators import body_pct, gap_pct, is_bull
from .triggers import Trigger


@dataclass
class Bar:
    """정밀 진단용 일봉 한 줄."""

    date: str
    role: str          # 트리거 / 조정n / 오늘
    close: float
    chg: float
    body: float
    shape: str         # 장대양봉 / 양봉 / 음봉 / 도지
    vol_vs_trigger: float   # 트리거일 거래량 대비 %
    gap_ma5: float
    gap_ma20: float


@dataclass
class Requirement:
    """내일 이 값을 넘겨야 패턴이 성립한다."""

    name: str
    text: str
    ok_today: bool = False   # 오늘 기준으로 이미 만족된 조건인가


@dataclass
class Outlook:
    code: str                       # A / B / C
    reachable: bool = False         # 내일 성립할 여지가 있는가
    reason: str = ""                # 없다면 그 이유
    last_chance: bool = False       # 내일이 마지막 기회인가
    requirements: list = field(default_factory=list)


def _shape(row, doji_body: float = 2.5, big_body: float = 12.0) -> str:
    b = body_pct(row)
    if not np.isfinite(b):
        return "-"
    if abs(b) <= doji_body:
        return "도지"
    if b >= big_body:
        return "장대양봉"
    return "양봉" if b > 0 else "음봉"


def timeline(df: pd.DataFrame, trig: Trigger, cfg: Config) -> list[Bar]:
    """트리거일부터 오늘까지의 일봉 흐름."""
    out: list[Bar] = []
    n = len(df)
    for i in range(trig.idx, n):
        row = df.iloc[i]
        prev = float(df["Close"].iloc[i - 1]) if i > 0 else np.nan
        close = float(row["Close"])
        if i == trig.idx:
            role = "트리거"
        elif i == n - 1:
            role = "오늘"
        else:
            role = f"조정{i - trig.idx}"
        out.append(Bar(
            date=str(df.index[i].date()),
            role=role,
            close=close,
            chg=(close / prev - 1.0) * 100.0 if np.isfinite(prev) and prev > 0 else np.nan,
            body=body_pct(row),
            shape=_shape(row, cfg.pattern_a.max_doji_body, cfg.trigger.big_body_pct),
            vol_vs_trigger=float(row["Volume"]) / trig.volume * 100.0 if trig.volume > 0 else np.nan,
            gap_ma5=gap_pct(close, float(row.get("ma5", np.nan))),
            gap_ma20=gap_pct(close, float(row.get("ma20", np.nan))),
        ))
    return out


def ma_floor(closes: pd.Series, window: int, tolerance: float = 0.0) -> float:
    """내일 종가가 이 값 이상이면 window일선 위(허용치 tolerance%)에서 마감한다.

    C ≥ (직전 window-1일 종가합 + C)/window × (1 - t) 를 C에 대해 푼 값.
    """
    prev = closes.iloc[-(window - 1):]
    if len(prev) < window - 1:
        return float("nan")
    s = float(prev.sum())
    t = tolerance / 100.0
    denom = (window - 1) + t
    return s * (1.0 - t) / denom if denom > 0 else float("nan")


def _vol_cap(seg_vol: pd.Series, trig_vol: float, ratio: float) -> float:
    """내일 거래량이 이 값 이하여야 '조정 구간 평균 ≤ 트리거일 × ratio'가 유지된다."""
    n = len(seg_vol)
    return trig_vol * ratio * (n + 1) - float(seg_vol.sum())


def _outlook_a(df: pd.DataFrame, trig: Trigger, cfg: Config) -> Outlook:
    c = cfg.pattern_a
    i = len(df) - 1
    d = i - trig.idx + 1                      # 내일이 조정 며칠째인가
    o = Outlook("A", last_chance=(d == c.max_bars))
    if d < c.min_bars:
        o.reason = f"내일이 조정 {d}일째 — 기준 {c.min_bars}~{c.max_bars}일에 아직 못 미침"
        return o
    if d > c.max_bars:
        o.reason = f"내일이면 조정 {d}일째 — 기준 {c.max_bars}일 초과 (패턴 A 기회 종료)"
        return o

    o.reachable = True
    closes = df["Close"]
    floor_ma = ma_floor(closes, c.ma_support, c.ma_tolerance)
    floor_high = trig.high * (1.0 - c.max_drop_from_high / 100.0)
    floor = max(floor_ma, floor_high)
    o.requirements.append(Requirement(
        "종가 하한",
        f"{floor:,.0f}원 이상 "
        f"(5일선 {floor_ma:,.0f} / 장대양봉 고가 -{c.max_drop_from_high:.0f}% {floor_high:,.0f} 중 높은 쪽)",
        float(closes.iloc[-1]) >= floor,
    ))

    seg = df.iloc[trig.idx + 1:]
    cap = _vol_cap(seg["Volume"], trig.volume, c.max_vol_vs_trigger)
    if c.require_vol_decline and len(seg):
        cap = min(cap, float(seg["Volume"].iloc[0]))
    if cap <= 0:
        o.reachable = False
        o.requirements = []
        o.reason = ("조정 구간 거래량이 이미 기준을 넘어섬 — 내일 거래량이 얼마든 "
                    f"'평균 ≤ 트리거일의 {c.max_vol_vs_trigger * 100:.0f}%'를 못 맞춤")
        return o
    o.requirements.append(Requirement(
        "거래량 상한", f"{cap:,.0f}주 이하 (오늘 {float(df['Volume'].iloc[-1]):,.0f}주)", True,
    ))

    trig_range = trig.high - trig.low
    o.requirements.append(Requirement(
        "캔들 모양",
        f"몸통 ±{c.max_doji_body:.1f}% 이내 · 캔들 길이 {trig_range * c.max_range_ratio:,.0f}원 이하 (도지)",
    ))
    return o


def _outlook_b(df: pd.DataFrame, trig: Trigger, cfg: Config) -> Outlook:
    c = cfg.pattern_b
    i = len(df) - 1
    d = i - trig.idx + 1
    o = Outlook("B", last_chance=(d == c.max_bars))
    if d < c.min_bars:
        o.reason = f"내일이 조정 {d}일째 — 기준 {c.min_bars}~{c.max_bars}일에 아직 못 미침"
        return o
    if d > c.max_bars:
        o.reason = f"내일이면 조정 {d}일째 — 기준 {c.max_bars}일 초과 (패턴 B 기회 종료)"
        return o

    down = df.iloc[trig.idx + 1:]             # 내일이 반등 양봉이므로 오늘까지가 하락 구간
    from .indicators import is_bear

    n_bear = int(sum(is_bear(down.iloc[k]) for k in range(len(down))))
    if n_bear < c.min_down_bars:
        o.reason = (f"음봉이 {len(down)}봉 중 {n_bear}개뿐 — 기준 {c.min_down_bars}개 이상 "
                    f"(4음 1양 형태가 아님)")
        return o

    o.reachable = True
    closes = df["Close"]
    floor = ma_floor(closes, c.ma_support, c.ma_tolerance)
    o.requirements.append(Requirement(
        "종가 하한", f"{floor:,.0f}원 이상 ({c.ma_support}일선 지지) · 반드시 양봉",
        float(closes.iloc[-1]) >= floor,
    ))

    ma20 = float(df["ma20"].iloc[-1])
    o.requirements.append(Requirement(
        "저가 하한",
        f"{ma20 * (1 - c.max_ma_undercut / 100.0):,.0f}원 이상 "
        f"(20일선을 {c.max_ma_undercut:.0f}% 넘게 깨면 탈락)",
    ))

    cap = _vol_cap(df.iloc[trig.idx + 1:]["Volume"], trig.volume, c.max_vol_vs_trigger)
    if cap <= 0:
        o.reachable = False
        o.requirements = []
        o.reason = ("조정 구간 거래량이 이미 기준을 넘어섬 — 내일 거래량이 얼마든 "
                    f"'평균 ≤ 트리거일의 {c.max_vol_vs_trigger * 100:.0f}%'를 못 맞춤")
        return o
    o.requirements.append(Requirement(
        "거래량 상한", f"{cap:,.0f}주 이하 (조정 구간 평균 기준)", True,
    ))
    return o


def _outlook_c(df: pd.DataFrame, trig: Trigger, cfg: Config) -> Outlook:
    c = cfg.pattern_c
    i = len(df) - 1
    d = i - trig.idx + 1
    o = Outlook("C", last_chance=(d == c.max_bars))
    if d < c.min_bars:
        o.reason = f"내일이 조정 {d}봉째 — 기준 {c.min_bars}~{c.max_bars}봉에 아직 못 미침"
        return o
    if d > c.max_bars:
        o.reason = f"내일이면 조정 {d}봉째 — 기준 {c.max_bars}봉 초과 (패턴 C 기회 종료)"
        return o

    last = df.iloc[-1]
    if float(last["Close"]) > float(last.get("ma5", np.nan)):
        o.reason = "오늘 이미 5일선 위 — '아래에서 올라타는' 복귀 양봉이 성립하지 않음"
        return o

    o.reachable = True
    closes = df["Close"]
    floor = ma_floor(closes, c.ma_reclaim, 0.0)
    o.requirements.append(Requirement(
        "종가 하한", f"{floor:,.0f}원 초과 ({c.ma_reclaim}일선 복귀) · 반드시 양봉",
    ))

    base = df.iloc[trig.idx + 1: i + 1]
    base_vol = float(base["Volume"].mean()) if len(base) else np.nan
    need = base_vol * c.vol_revive_mult
    cap = _vol_cap(base["Volume"], trig.volume, c.max_vol_vs_trigger)
    if not np.isfinite(need) or cap <= need:
        o.reachable = False
        o.requirements = []
        o.reason = (f"거래량 재증가({need:,.0f}주 이상)와 극감 유지({max(cap, 0):,.0f}주 이하)가 "
                    "동시에 성립하지 않음")
        return o
    o.requirements.append(Requirement(
        "거래량",
        f"{need:,.0f} ~ {cap:,.0f}주 (조정 평균 {base_vol:,.0f}주의 "
        f"{c.vol_revive_mult:.1f}배 이상, 극감 기준은 유지)",
    ))

    ma20 = float(last.get("ma20", np.nan))
    o.requirements.append(Requirement(
        "저가 하한",
        f"{ma20 * (1 - c.max_ma_undercut / 100.0):,.0f}원 이상 "
        f"(20일선을 {c.max_ma_undercut:.0f}% 넘게 깨면 탈락)",
    ))
    return o


def next_day(df: pd.DataFrame, trig: Trigger, cfg: Config) -> list[Outlook]:
    """내일 각 패턴이 성립하려면 무엇이 필요한지. 성립 여지가 있는 것부터."""
    outs = [_outlook_a(df, trig, cfg), _outlook_b(df, trig, cfg), _outlook_c(df, trig, cfg)]
    return sorted(outs, key=lambda o: (not o.reachable, o.code))
