"""매도 타점 · 재진입 판정.

  익절  장대양봉 2연속 또는 이틀 50% 이상 분출 → 단기 꼭지로 보고 고점 매도
  손절  5일선(단기 패턴) / 20일선(기간 조정) 하향 이탈
  재진입 세력주가 5일선을 깼어도 10일선 지지 후 5일선을 되찾으면 2차 매수
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .config import ExitConfig
from .indicators import body_pct, gap_pct, is_bull

KIND_ORDER = {"익절": 0, "손절": 1, "재진입": 2, "경고": 3}


@dataclass
class Signal:
    kind: str        # 익절 / 손절 / 재진입 / 경고
    rule: str        # 어떤 규칙에 걸렸는지
    detail: str
    level: float = np.nan   # 관련 가격 (손절선 등)

    def __str__(self) -> str:
        return f"[{self.kind}] {self.rule} — {self.detail}"


def _broke_below(df: pd.DataFrame, col: str) -> bool:
    """어제까지는 위였는데 오늘 종가로 이탈했는가."""
    if len(df) < 2 or col not in df:
        return False
    cur, prev = df.iloc[-1], df.iloc[-2]
    ma_cur, ma_prev = float(cur.get(col, np.nan)), float(prev.get(col, np.nan))
    if not (np.isfinite(ma_cur) and np.isfinite(ma_prev)):
        return False
    return float(prev["Close"]) >= ma_prev and float(cur["Close"]) < ma_cur


def blowoff(df: pd.DataFrame, cfg: ExitConfig) -> list[Signal]:
    """단기 꼭지 — 분출이 나왔으니 들고 있으면 던지라는 신호."""
    out: list[Signal] = []
    if len(df) < 3:
        return out
    last, prev = df.iloc[-1], df.iloc[-2]

    b1, b2 = body_pct(last), body_pct(prev)
    if is_bull(last) and is_bull(prev) and b1 >= cfg.blowoff_body_pct and b2 >= cfg.blowoff_body_pct:
        out.append(Signal(
            "익절", "장대양봉 2연속",
            f"어제 {b2:+.1f}% · 오늘 {b1:+.1f}% (기준 {cfg.blowoff_body_pct:.0f}% 이상)",
            float(last["Close"]),
        ))

    base = float(df["Close"].iloc[-3])
    if base > 0:
        gain2 = (float(last["Close"]) / base - 1.0) * 100.0
        if gain2 >= cfg.blowoff_2day_gain:
            out.append(Signal(
                "익절", "이틀 시세 분출",
                f"2일 누적 {gain2:+.0f}% (기준 {cfg.blowoff_2day_gain:.0f}% 이상)",
                float(last["Close"]),
            ))
    return out


def stop_loss(df: pd.DataFrame, cfg: ExitConfig, basis: str = "auto") -> list[Signal]:
    """이동평균 하향 이탈. basis 는 '5일선' / '20일선' / 'auto'(둘 다 검사)."""
    out: list[Signal] = []
    last = df.iloc[-1]
    targets = []
    if basis in ("auto", f"{cfg.stop_ma_fast}일선"):
        targets.append(cfg.stop_ma_fast)
    if basis in ("auto", f"{cfg.stop_ma_slow}일선"):
        targets.append(cfg.stop_ma_slow)

    for w in targets:
        col = f"ma{w}"
        if _broke_below(df, col):
            ma = float(last.get(col, np.nan))
            out.append(Signal(
                "손절", f"{w}일선 이탈",
                f"종가 {float(last['Close']):,.0f} < {w}일선 {ma:,.0f} ({gap_pct(float(last['Close']), ma):+.1f}%)",
                ma,
            ))
    return out


def reentry(df: pd.DataFrame, cfg: ExitConfig) -> list[Signal]:
    """세력주 2차 매수 — 5일선을 깼다가 10일선 지지 후 5일선 복귀."""
    out: list[Signal] = []
    n = len(df)
    if n < cfg.reentry_lookback + 2:
        return out

    last = df.iloc[-1]
    close = float(last["Close"])
    ma5, ma10 = float(last.get("ma5", np.nan)), float(last.get(f"ma{cfg.reentry_ma}", np.nan))
    if not (np.isfinite(ma5) and np.isfinite(ma10)):
        return out

    look = df.iloc[-cfg.reentry_lookback - 1: -1]
    broke5 = bool((look["Close"] < look["ma5"]).any())
    floor = ma10 * (1.0 - cfg.reentry_tolerance / 100.0)
    held10 = bool((look["Low"].min() >= floor) and (look["Close"] >= look[f"ma{cfg.reentry_ma}"] * 0.98).any())
    reclaimed = is_bull(last) and close > ma5 and float(df["Close"].iloc[-2]) <= float(df["ma5"].iloc[-2])

    if broke5 and held10 and reclaimed:
        out.append(Signal(
            "재진입", f"{cfg.reentry_ma}일선 지지 후 5일선 복귀",
            f"최근 {cfg.reentry_lookback}일 중 5일선 이탈 → {cfg.reentry_ma}일선 {ma10:,.0f} 지지 → 오늘 종가 {close:,.0f}",
            ma5,
        ))
    return out


def evaluate(df: pd.DataFrame, cfg: ExitConfig, basis: str = "auto") -> list[Signal]:
    """보유 종목 하나에 대한 청산·재진입 신호 전부."""
    sigs = blowoff(df, cfg) + stop_loss(df, cfg, basis) + reentry(df, cfg)
    return sorted(sigs, key=lambda s: KIND_ORDER.get(s.kind, 9))
