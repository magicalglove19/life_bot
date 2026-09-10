"""추적 시작 사건 탐지 — 상한가 / 장대양봉 / 신고가.

강창권 기법의 출발점은 "세력이 들어왔다는 흔적"이다. 그 흔적을
상한가, 대량 거래를 동반한 장대양봉, 신고가 돌파 세 가지로 정의한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import TriggerConfig
from .indicators import body_pct, body_ratio, is_bull


@dataclass
class Trigger:
    idx: int                     # df 안에서의 위치 인덱스
    date: object
    kind: str                    # 상한가 / 장대양봉 / 신고가
    change_pct: float            # 전일 종가 대비 등락률
    body: float                  # 몸통 %
    open: float = np.nan
    high: float = np.nan
    low: float = np.nan
    close: float = np.nan
    volume: float = np.nan
    vol_mult: float = np.nan     # 20일 평균 거래량 대비 배수
    highs: list = field(default_factory=list)   # 동시에 달성한 신고가 라벨 (5일/20일/52주)

    @property
    def strength(self) -> int:
        """트리거의 급 — 상한가 4, 장대양봉 3, 급등 2, 신고가 1."""
        return {"상한가": 4, "장대양봉": 3, "급등": 2, "신고가": 1}.get(self.kind, 0)

    @property
    def label(self) -> str:
        hi = ("+" + "/".join(self.highs)) if self.highs else ""
        return f"{self.kind}{hi}"


def _high_labels(df: pd.DataFrame, i: int, cfg: TriggerConfig) -> list[str]:
    """i번째 봉이 달성한 신고가 라벨."""
    close = df["Close"].astype(float)
    out = []
    for bars in cfg.high_lookbacks:
        if i + 1 < 3:
            continue
        window = close.iloc[max(0, i + 1 - bars): i + 1]
        if len(window) < min(bars, 3):
            continue
        if float(close.iloc[i]) >= float(window.max()):
            out.append("52주" if bars >= 250 else f"{bars}일")
    return out


def classify(df: pd.DataFrame, i: int, cfg: TriggerConfig) -> Trigger | None:
    """i번째 봉이 트리거인지 판정한다. 아니면 None."""
    if i <= 0 or i >= len(df):
        return None
    row = df.iloc[i]
    prev_close = float(df["Close"].iloc[i - 1])
    if prev_close <= 0:
        return None

    chg = (float(row["Close"]) / prev_close - 1.0) * 100.0
    body = body_pct(row)
    vol = float(row["Volume"])
    vol20 = float(row.get("vol20", np.nan))
    vol_mult = vol / vol20 if np.isfinite(vol20) and vol20 > 0 else np.nan
    highs = _high_labels(df, i, cfg)

    kind = None
    if chg >= cfg.limit_up_pct:
        kind = "상한가"
    elif (
        is_bull(row)
        and body >= cfg.big_body_pct
        and body_ratio(row) >= cfg.big_body_ratio
        and np.isfinite(vol_mult)
        and vol_mult >= cfg.big_vol_mult
    ):
        kind = "장대양봉"
    elif (
        chg >= cfg.surge_pct
        and is_bull(row)
        and np.isfinite(vol_mult)
        and vol_mult >= cfg.surge_vol_mult
    ):
        kind = "급등"
    elif "52주" in highs and is_bull(row) and np.isfinite(vol_mult) and vol_mult >= 1.5:
        kind = "신고가"

    if kind is None:
        return None

    return Trigger(
        idx=i,
        date=df.index[i],
        kind=kind,
        change_pct=chg,
        body=body,
        open=float(row["Open"]),
        high=float(row["High"]),
        low=float(row["Low"]),
        close=float(row["Close"]),
        volume=vol,
        vol_mult=vol_mult,
        highs=highs,
    )


def find_recent(df: pd.DataFrame, cfg: TriggerConfig) -> list[Trigger]:
    """최근 max_age 거래일 안에서 발생한 트리거를 최신순으로 반환."""
    n = len(df)
    if n < 25:
        return []
    start = max(1, n - 1 - cfg.max_age)
    out = [t for i in range(start, n) if (t := classify(df, i, cfg))]
    return sorted(out, key=lambda t: t.idx, reverse=True)


def today_trigger(df: pd.DataFrame, cfg: TriggerConfig) -> Trigger | None:
    """오늘(마지막 봉)이 트리거인가 — 관심종목 자동 등록용."""
    return classify(df, len(df) - 1, cfg)
