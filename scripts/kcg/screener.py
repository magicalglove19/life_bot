"""전체 스캔 오케스트레이션.

  1. 유동성 필터를 통과한 종목마다 최근 15거래일 안의 트리거를 찾는다
  2. 트리거 × 패턴(A/B/C) 을 전부 돌려 오늘 종가 매수 시그널을 만든다
  3. 오늘 새로 터진 상한가·신고가는 '추적 관찰' 목록으로 분리한다
  4. 성립하진 않았지만 조정이 진행 중인 종목은 '관찰 중'으로 남긴다
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import exits, patterns, risk, triggers
from .config import Config
from .indicators import gap_pct
from .patterns import PatternResult
from .triggers import Trigger


@dataclass
class Candidate:
    code: str
    name: str
    market: str = ""
    sector: str = ""

    price: float = np.nan
    chg: float = np.nan            # 당일 등락률 %
    value: float = np.nan          # 당일 거래대금 (원)
    vol_mult: float = np.nan       # 20일 평균 거래량 대비
    gap_ma5: float = np.nan
    gap_ma20: float = np.nan

    trigger: Trigger | None = None       # 이 종목을 추적하게 만든 사건
    today_trigger: Trigger | None = None  # 오늘 발생한 트리거 (추적 등록용)
    pattern: PatternResult | None = None  # 성립한 최고 점수 패턴
    near_miss: PatternResult | None = None
    signals: list = field(default_factory=list)   # 익절/손절/재진입/경고

    @property
    def score(self) -> float:
        return self.pattern.score if self.pattern else (self.near_miss.score if self.near_miss else 0.0)

    @property
    def buy_price(self) -> float:
        return self.pattern.buy_price if self.pattern else self.price

    @property
    def stop_price(self) -> float:
        return self.pattern.stop_price if self.pattern else np.nan

    @property
    def stop_pct(self) -> float:
        return self.pattern.stop_pct if self.pattern else np.nan

    @property
    def days_since_trigger(self) -> int:
        return self.pattern.bars_since_trigger if self.pattern else (
            self.near_miss.bars_since_trigger if self.near_miss else 0
        )


@dataclass
class ScanResult:
    buys: list = field(default_factory=list)      # 오늘 종가 매수 시그널
    watch: list = field(default_factory=list)     # 오늘 터진 상한가/신고가 → 추적 등록
    tracking: list = field(default_factory=list)  # 조정 진행 중 (아직 타점 아님)
    scanned: int = 0
    skipped: int = 0


def _liquid(df: pd.DataFrame, cfg: Config) -> bool:
    """동전주·거래 없는 종목 제외."""
    last = df.iloc[-1]
    if float(last["Close"]) < cfg.min_price:
        return False
    value20 = float(df["Close"].iloc[-20:].mul(df["Volume"].iloc[-20:]).mean())
    return np.isfinite(value20) and value20 >= cfg.min_avg_trade_value


def _fill(c: Candidate, df: pd.DataFrame) -> None:
    last = df.iloc[-1]
    c.price = float(last["Close"])
    c.chg = float(last.get("chg", np.nan))
    c.value = float(last["Close"]) * float(last["Volume"])
    vol20 = float(last.get("vol20", np.nan))
    c.vol_mult = float(last["Volume"]) / vol20 if np.isfinite(vol20) and vol20 > 0 else np.nan
    c.gap_ma5 = gap_pct(c.price, float(last.get("ma5", np.nan)))
    c.gap_ma20 = gap_pct(c.price, float(last.get("ma20", np.nan)))


def scan(frames: dict[str, pd.DataFrame], meta: dict[str, dict], cfg: Config) -> ScanResult:
    res = ScanResult()

    for code, df in frames.items():
        if len(df) < 30:
            res.skipped += 1
            continue
        if not _liquid(df, cfg):
            res.skipped += 1
            continue
        res.scanned += 1

        info = meta.get(code, {})
        c = Candidate(code=code, name=info.get("name", code),
                      market=info.get("market", ""), sector=info.get("sector", ""))
        _fill(c, df)

        today = triggers.today_trigger(df, cfg.trigger)
        if today is not None:
            c.today_trigger = today
            res.watch.append(c)

        found = triggers.find_recent(df, cfg.trigger)
        # 오늘 봉 자체는 조정의 출발점이므로 패턴 판정에서는 제외한다
        past = [t for t in found if t.idx < len(df) - 1]
        if not past:
            continue

        hits = patterns.detect_all(df, past, cfg)
        if hits:
            c.pattern = hits[0]
            c.trigger = hits[0].trigger
            c.signals = exits.blowoff(df, cfg.exit) + risk.chase_warnings(df, cfg.risk)
            if c.pattern.score >= cfg.min_score:
                res.buys.append(c)
            else:
                res.tracking.append(c)
            continue

        near = patterns.best_near_miss(df, past, cfg)
        if near is not None:
            c.near_miss = near
            c.trigger = near.trigger
            res.tracking.append(c)

    res.buys.sort(key=lambda c: (c.score, c.value), reverse=True)
    res.watch.sort(key=lambda c: (c.today_trigger.strength, c.value), reverse=True)
    res.tracking.sort(key=lambda c: c.score, reverse=True)
    return res


def review_positions(frames: dict[str, pd.DataFrame], meta: dict[str, dict],
                     positions: list[dict], cfg: Config) -> list[Candidate]:
    """보유 종목에 대한 청산·재진입 판정.

    positions 는 [{'code','name','basis'}] — basis 는 '5일선'/'20일선'/'auto'.
    """
    out: list[Candidate] = []
    for p in positions:
        code = p["code"]
        df = frames.get(code)
        if df is None or len(df) < 30:
            continue
        info = meta.get(code, {})
        c = Candidate(code=code, name=p.get("name") or info.get("name", code),
                      market=info.get("market", ""), sector=info.get("sector", ""))
        _fill(c, df)
        c.signals = exits.evaluate(df, cfg.exit, p.get("basis", "auto"))
        out.append(c)
    return out


def to_dataframe(cands: list[Candidate]) -> pd.DataFrame:
    rows = []
    for c in cands:
        p = c.pattern or c.near_miss
        rows.append({
            "종목코드": c.code,
            "종목명": c.name,
            "시장": c.market,
            "업종": c.sector,
            "현재가": round(c.price),
            "등락률": round(c.chg, 2) if np.isfinite(c.chg) else None,
            "거래대금(억)": round(c.value / 1e8, 1) if np.isfinite(c.value) else None,
            "패턴": p.code if p else "",
            "패턴명": p.label if p else "",
            "점수": p.score if p else None,
            "트리거": c.trigger.label if c.trigger else (c.today_trigger.label if c.today_trigger else ""),
            "트리거일": str(c.trigger.date.date()) if c.trigger is not None else "",
            "경과일": c.days_since_trigger,
            "매수가": round(c.buy_price) if np.isfinite(c.buy_price) else None,
            "손절선": round(c.stop_price) if np.isfinite(c.stop_price) else None,
            "손절폭": round(c.stop_pct, 1) if np.isfinite(c.stop_pct) else None,
            "5일선이격": round(c.gap_ma5, 1) if np.isfinite(c.gap_ma5) else None,
            "20일선이격": round(c.gap_ma20, 1) if np.isfinite(c.gap_ma20) else None,
            "미충족": p.note if p and not p.matched else "",
        })
    return pd.DataFrame(rows)
