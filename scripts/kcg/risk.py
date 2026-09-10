"""리스크 관리 — 보유 종목 수 제한과 추격매수 경고."""

from __future__ import annotations

import numpy as np
import pandas as pd

from .config import RiskConfig
from .exits import Signal
from .indicators import gap_pct


def portfolio_warnings(n_positions: int, cfg: RiskConfig) -> list[Signal]:
    """보유 종목 수가 원칙을 넘었는지."""
    out: list[Signal] = []
    if n_positions > cfg.max_positions:
        out.append(Signal(
            "경고", "보유 종목 수 초과",
            f"{n_positions}개 보유 (원칙 {cfg.max_positions}개 이하). "
            f"신규 매수 전에 부진한 종목부터 정리하세요.",
        ))
    elif n_positions >= cfg.max_positions:
        out.append(Signal(
            "경고", "보유 종목 수 한도",
            f"{n_positions}개 보유 — 한 자리 더 늘리면 밀착 관리가 어렵습니다 "
            f"(권장 {cfg.ideal_positions}~{cfg.max_positions}개).",
        ))
    return out


def chase_warnings(df: pd.DataFrame, cfg: RiskConfig) -> list[Signal]:
    """지금 사면 추격매수가 되는 상태인지 — 이격도와 당일 급등률로 판정."""
    out: list[Signal] = []
    if len(df) < 2:
        return out
    last = df.iloc[-1]
    close = float(last["Close"])
    prev = float(df["Close"].iloc[-2])
    chg = (close / prev - 1.0) * 100.0 if prev > 0 else np.nan

    if np.isfinite(chg) and chg >= cfg.chase_day_gain:
        out.append(Signal(
            "경고", "당일 급등 추격매수",
            f"오늘 {chg:+.1f}% (기준 {cfg.chase_day_gain:.0f}% 이상). "
            f"눌림목을 기다리는 것이 원칙입니다.",
        ))

    g5 = gap_pct(close, float(last.get("ma5", np.nan)))
    if np.isfinite(g5) and g5 >= cfg.chase_ma5_gap:
        out.append(Signal(
            "경고", "5일선 이격 과대",
            f"5일선 대비 {g5:+.1f}% (기준 {cfg.chase_ma5_gap:.0f}% 이상)",
        ))

    g20 = gap_pct(close, float(last.get("ma20", np.nan)))
    if np.isfinite(g20) and g20 >= cfg.chase_ma20_gap:
        out.append(Signal(
            "경고", "20일선 이격 과대",
            f"20일선 대비 {g20:+.1f}% (기준 {cfg.chase_ma20_gap:.0f}% 이상)",
        ))
    return out


def scout_message(name: str, code: str, price: float, cfg: RiskConfig) -> str:
    """상한가·신고가 종목을 관심종목에 넣을 때의 안내 문구."""
    return (
        f"{name}({code}) 추적 관찰 — {cfg.scout_shares}주만 정찰 매수해 두면 "
        f"매일 눈에 들어옵니다 (현재가 {price:,.0f}원). "
        f"본 매수는 조정 패턴(A/B/C)이 완성되는 날 종가에 합니다."
    )
