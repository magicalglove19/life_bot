"""세 줄 기법 — 바닥권에서 세력이 들어온 다음 날 종가에 산다.

  1일차  기준봉: 거래대금 500억 이상 + 위치(200일선 바닥권 / 50일 하향추세선 돌파 /
         이평선 밀집 돌파) 중 하나
  2일차  오늘: 갭상승 음봉(조건 A) 또는 긴 윗꼬리(조건 B) → 종가 매수

매수는 2일차 종가 마감 직전(15:20~15:30), 놓치면 3일차 시초가.
손절은 기준봉 저가 이탈, 익절은 파동이 나오면 즉시.

200일선·50일선은 여기서 직접 계산한다 — indicators.py 는 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config, ThreeLineConfig
from .indicators import gap_pct, is_bull
from .patterns import Check

SETUP_LABEL = {
    "바닥권": "200일선 바닥권",
    "추세돌파": "50일 하향추세선 돌파",
    "밀집돌파": "이평선 밀집 돌파",
}
ENTRY_LABEL = {"A": "갭상승 음봉", "B": "긴 윗꼬리"}


@dataclass
class ThreeLinePick:
    code: str
    name: str
    market: str = ""
    sector: str = ""
    entry: str = ""                    # A / B
    setups: list = field(default_factory=list)   # 바닥권 / 추세돌파 / 밀집돌파
    matched: bool = False
    score: float = 0.0

    price: float = np.nan              # 오늘 종가 = 매수가
    chg: float = np.nan
    value: float = np.nan              # 오늘 거래대금
    base_date: str = ""                # 기준봉 날짜
    base_value: float = np.nan         # 기준봉 거래대금
    base_chg: float = np.nan
    base_low: float = np.nan           # 손절선
    stop_pct: float = np.nan
    gap: float = np.nan                # 오늘 갭 %
    wick: float = np.nan               # 윗꼬리 / 캔들 길이
    ma200_gap: float = np.nan
    checks: list = field(default_factory=list)

    sector_peers: int = 0              # 같은 업종 동반 상승 종목 수
    # enrich.py 가 채운다
    news: list = field(default_factory=list)
    news_checked: bool = False
    marcap: float = np.nan
    foreign: float = np.nan
    organ: float = np.nan

    @property
    def label(self) -> str:
        return ENTRY_LABEL.get(self.entry, "")

    @property
    def setup_label(self) -> str:
        return " · ".join(SETUP_LABEL.get(s, s) for s in self.setups)

    @property
    def note(self) -> str:
        return "; ".join(f"{c.name} {c.detail}" for c in self.checks if not c.ok)


def _mas(df: pd.DataFrame, cfg: ThreeLineConfig) -> tuple[pd.Series, pd.Series]:
    close = df["Close"].astype(float)
    long_ = close.rolling(cfg.ma_long, min_periods=cfg.ma_long // 2).mean()
    mid = close.rolling(cfg.ma_mid, min_periods=cfg.ma_mid // 2).mean()
    return long_, mid


def _setups(df: pd.DataFrame, i: int, cfg: ThreeLineConfig) -> tuple[list[str], float, str]:
    """기준봉이 선 자리 — 바닥권 / 50일 하향추세선 돌파 / 이평선 밀집 돌파."""
    long_, mid = _mas(df, cfg)
    close = float(df["Close"].iloc[i])
    prev_close = float(df["Close"].iloc[i - 1])
    ma200, ma50 = float(long_.iloc[i]), float(mid.iloc[i])
    ma5, ma20 = float(df["ma5"].iloc[i]), float(df["ma20"].iloc[i])
    g200 = gap_pct(close, ma200)

    found, detail = [], []
    if np.isfinite(g200) and abs(g200) <= cfg.bottom_band:
        found.append("바닥권")
        detail.append(f"200일선 이격 {g200:+.1f}%")

    if np.isfinite(ma50) and i >= cfg.mid_slope_bars:
        falling = ma50 < float(mid.iloc[i - cfg.mid_slope_bars])
        crossed = prev_close < float(mid.iloc[i - 1]) <= ma50 and close > ma50
        if falling and crossed:
            found.append("추세돌파")
            detail.append(f"하락하던 50일선 {ma50:,.0f} 상방 돌파")

    mas = [m for m in (ma5, ma20, ma50, ma200) if np.isfinite(m) and m > 0]
    if len(mas) == 4:
        spread = (max(mas) / min(mas) - 1.0) * 100.0
        if spread <= cfg.cluster_spread and close > max(mas):
            found.append("밀집돌파")
            detail.append(f"5·20·50·200일선이 {spread:.1f}% 안에 밀집")
    return found, g200, " · ".join(detail)


def _wick(row) -> tuple[float, float]:
    hi, lo, c = float(row["High"]), float(row["Low"]), float(row["Close"])
    wick = hi - max(float(row["Open"]), c)
    rng = hi - lo
    return (wick / rng if rng > 0 else 0.0), (wick / c * 100.0 if c > 0 else 0.0)


W = {"손절폭": 2, "1일차 거래대금": 3, "1일차 등락률": 1, "1일차 거래량": 1, "1일차 자리": 3,
     "2일차 캔들": 3, "2일차 기준봉 지지": 2, "섹터 동반 상승": 1}


def detect(df: pd.DataFrame, code: str, info: dict, cfg: ThreeLineConfig,
           sector_peers: int = 0) -> ThreeLinePick:
    """오늘이 2일차 종가 매수 타점인지."""
    n = len(df)
    last = df.iloc[-1]
    base_i = n - 1 - cfg.base_within          # 기준봉 = 어제
    base = df.iloc[base_i]

    close = float(last["Close"])
    prev = float(df["Close"].iloc[-2])
    base_close = float(base["Close"])
    base_prev = float(df["Close"].iloc[base_i - 1])
    base_value = base_close * float(base["Volume"])
    base_chg = (base_close / base_prev - 1.0) * 100.0 if base_prev > 0 else np.nan
    base_vol20 = float(base.get("vol20", np.nan))
    base_vol_mult = float(base["Volume"]) / base_vol20 if base_vol20 > 0 else np.nan

    setups, g200, setup_detail = _setups(df, base_i, cfg)
    gap = (float(last["Open"]) / prev - 1.0) * 100.0 if prev > 0 else np.nan
    wick_ratio, wick_pct = _wick(last)

    entry_a = gap >= cfg.gap_pct and not is_bull(last)
    entry_b = wick_ratio >= cfg.wick_ratio and wick_pct >= cfg.wick_pct
    entry = "A" if entry_a else ("B" if entry_b else "")

    p = ThreeLinePick(
        code=code, name=info.get("name", code), market=info.get("market", ""),
        sector=info.get("sector", ""), entry=entry, setups=setups,
        price=close, chg=(close / prev - 1.0) * 100.0 if prev > 0 else np.nan,
        value=close * float(last["Volume"]),
        base_date=str(df.index[base_i].date()), base_value=base_value, base_chg=base_chg,
        base_low=float(base["Low"]), gap=gap, wick=wick_ratio, ma200_gap=g200,
        sector_peers=sector_peers,
    )
    p.stop_pct = (p.base_low / close - 1.0) * 100.0

    p.checks = [
        Check("1일차 거래대금", base_value >= cfg.base_value,
              f"{p.base_date} {base_value / 1e8:,.0f}억 "
              f"(기준 {cfg.base_value / 1e8:,.0f}억)"),
        Check("1일차 등락률", np.isfinite(base_chg) and base_chg >= cfg.base_min_chg,
              f"{base_chg:+.1f}% (기준 +{cfg.base_min_chg:.0f}%)"),
        Check("1일차 거래량", np.isfinite(base_vol_mult) and base_vol_mult >= cfg.base_vol_mult,
              f"20일 평균의 {base_vol_mult:.1f}배 (기준 {cfg.base_vol_mult:.0f}배)", critical=False),
        Check("1일차 자리", bool(setups),
              setup_detail if setups else f"200일선 이격 {g200:+.1f}% — 바닥권도 돌파도 아님"),
        Check("2일차 캔들", bool(entry),
              (f"갭 {gap:+.1f}% 음봉" if entry == "A" else
               f"윗꼬리 캔들 (캔들의 {wick_ratio * 100:.0f}% · {wick_pct:.1f}%)") if entry
              else f"갭 {gap:+.1f}% · 윗꼬리 {wick_ratio * 100:.0f}% — 둘 다 아님"),
        Check("2일차 기준봉 지지", close >= base_close * (1 - cfg.max_drop / 100.0),
              f"종가 {close:,.0f} vs 기준봉 종가 {base_close:,.0f} "
              f"({gap_pct(close, base_close):+.1f}%, 허용 -{cfg.max_drop:.0f}%)"),
        Check("손절폭", abs(p.stop_pct) <= cfg.max_stop_pct,
              f"기준봉 저가까지 {p.stop_pct:+.1f}% (기준 -{cfg.max_stop_pct:.0f}% 이내) — "
              f"깊으면 비중을 줄인다", critical=False),
        Check("섹터 동반 상승", sector_peers >= cfg.sector_peers,
              f"같은 업종에서 {sector_peers}종목 동반 상승 (기준 {cfg.sector_peers}종목)",
              critical=False),
    ]

    p.matched = all(c.ok for c in p.checks if c.critical)
    got = sum(W.get(c.name, 1.0) for c in p.checks if c.ok)
    total = sum(W.get(c.name, 1.0) for c in p.checks)
    p.score = round(got / total * 100.0, 1) if total > 0 else 0.0
    return p


def _sector_counts(frames: dict, meta: dict, cfg: ThreeLineConfig) -> dict[str, int]:
    """오늘 업종별로 몇 종목이나 동반 상승했나 — 주도 테마 판정용."""
    out: dict[str, int] = {}
    for code, df in frames.items():
        sector = meta.get(code, {}).get("sector", "")
        if not sector or len(df) < 2:
            continue
        prev = float(df["Close"].iloc[-2])
        if prev <= 0:
            continue
        chg = (float(df["Close"].iloc[-1]) / prev - 1.0) * 100.0
        if chg >= cfg.sector_chg:
            out[sector] = out.get(sector, 0) + 1
    return out


def scan(frames: dict[str, pd.DataFrame], meta: dict[str, dict], cfg: Config) -> list[ThreeLinePick]:
    tcfg = cfg.threeline
    peers = _sector_counts(frames, meta, tcfg)
    out: list[ThreeLinePick] = []
    for code, df in frames.items():
        df = df[(df["High"] > 0) & (df["Low"] > 0) & (df["Open"] > 0)]
        if len(df) < tcfg.ma_long // 2 + 5 or float(df["Close"].iloc[-1]) < cfg.min_price:
            continue
        info = meta.get(code, {})
        r = detect(df, code, info, tcfg, peers.get(info.get("sector", ""), 0))
        if r.matched:
            out.append(r)
    out.sort(key=lambda r: (r.score, r.base_value), reverse=True)
    return out


def rank(picks: list[ThreeLinePick], cfg: ThreeLineConfig) -> list[ThreeLinePick]:
    """재료(뉴스)가 확인되지 않은 종목을 뺀다 — 조회 실패는 남긴다."""
    keep = [p for p in picks if not (cfg.require_news and p.news_checked and not p.news)]
    return sorted(keep, key=lambda p: (p.score, p.base_value), reverse=True)


def to_dataframe(picks: list[ThreeLinePick]) -> pd.DataFrame:
    rows = []
    for p in picks:
        rows.append({
            "종목코드": p.code,
            "종목명": p.name,
            "시장": p.market,
            "업종": p.sector,
            "점수": p.score,
            "1일차(기준봉)": p.base_date,
            "1일차 거래대금(억)": round(p.base_value / 1e8),
            "1일차 등락": round(p.base_chg, 1),
            "1일차 자리": p.setup_label,
            "2일차 조건": f"{p.entry} {p.label}",
            "2일차 종가(매수가)": round(p.price),
            "2일차 등락": round(p.chg, 2),
            "2일차 갭": round(p.gap, 1),
            "2일차 윗꼬리(%)": round(p.wick * 100),
            "200일선이격": round(p.ma200_gap, 1) if np.isfinite(p.ma200_gap) else None,
            "손절(기준봉저가)": round(p.base_low),
            "손절폭": round(p.stop_pct, 1),
            "섹터동반": p.sector_peers,
            "재료": p.news[0][1] if p.news else ("뉴스 없음" if p.news_checked else "조회 실패"),
            "감점": p.note,
        })
    return pd.DataFrame(rows)
