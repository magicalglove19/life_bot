"""신정제 종가배팅 — 장 마감 직전 매수, 다음 날 장 초반 청산.

  유형 1  당일 강세 모멘텀 지속 — 대량 거래대금 + 신고가 근처 + 윗꼬리 없는 양봉
  유형 2  신고가 후 기간조정 재상승 — 저점을 높이며 쉬다가 5/20일선 눌림에서 양봉

일봉으로 가려지는 조건만 여기서 본다. 재료(뉴스)·시총·외인/기관 수급은
후보가 좁혀진 뒤 enrich.py 가 붙이고, 1분봉 타점·호가창·프로그램 매수는
15:18 에 HTS 에서 직접 확인한다 (intraday.py 에 판정 함수가 있다).

손절은 둘 다 같다 — 매수가(평단) 이탈, 당일 저가 이탈, 시간외 하락.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import ClosingConfig, Config
from .indicators import gap_pct, is_bull
from .patterns import Check

TYPE_LABEL = {1: "당일 강세 모멘텀", 2: "신고가 후 기간조정 재상승"}


@dataclass
class ClosingPick:
    code: str
    name: str
    market: str = ""
    kind: int = 0                      # 1 / 2
    matched: bool = False
    score: float = 0.0
    price: float = np.nan              # 판정 시점 종가(장중이면 현재가)
    chg: float = np.nan
    value: float = np.nan              # 오늘 거래대금
    vol_mult: float = np.nan           # 직전 20일 평균 대비
    day_low: float = np.nan            # 손절선 — 당일 저가
    high_gap: float = np.nan           # 전고점 대비 % (0 이상이면 신고가)
    checks: list = field(default_factory=list)

    # enrich.py 가 채운다
    news: list = field(default_factory=list)       # [(시각, 제목)]
    news_checked: bool = False
    marcap: float = np.nan
    foreign: float = np.nan                        # 전일 외국인 순매수 (주)
    organ: float = np.nan                          # 전일 기관 순매수 (주)

    @property
    def label(self) -> str:
        return TYPE_LABEL.get(self.kind, "")

    @property
    def note(self) -> str:
        return "; ".join(f"{c.name} {c.detail}" for c in self.checks if not c.ok)


def _upper_wick(row) -> tuple[float, float]:
    """윗꼬리 (종가 대비 %, 캔들 길이 대비 비율)."""
    hi, lo, c = float(row["High"]), float(row["Low"]), float(row["Close"])
    wick = hi - max(float(row["Open"]), c)
    rng = hi - lo
    return (wick / c * 100.0 if c > 0 else np.nan), (wick / rng if rng > 0 else 0.0)


def _base(df: pd.DataFrame, cfg: ClosingConfig) -> dict:
    last = df.iloc[-1]
    close = float(last["Close"])
    prev = float(df["Close"].iloc[-2])
    vol_prev20 = float(df["Volume"].iloc[-21:-1].mean())
    prior_high = float(df["High"].iloc[-cfg.high_lookback - 1:-1].max())
    wick_pct, wick_ratio = _upper_wick(last)
    return {
        "last": last,
        "close": close,
        "chg": (close / prev - 1.0) * 100.0 if prev > 0 else np.nan,
        "value": close * float(last["Volume"]),
        "vol_mult": float(last["Volume"]) / vol_prev20 if vol_prev20 > 0 else np.nan,
        "prior_high": prior_high,
        "high_gap": gap_pct(close, prior_high),
        "wick_pct": wick_pct,
        "wick_ratio": wick_ratio,
    }


def _candle_check(b: dict, cfg: ClosingConfig) -> Check:
    ok = (is_bull(b["last"]) and b["wick_pct"] <= cfg.max_upper_wick_pct
          and b["wick_ratio"] <= cfg.max_upper_wick_ratio)
    return Check("윗꼬리 없는 양봉", bool(ok),
                 f"{'양봉' if is_bull(b['last']) else '음봉'} · 윗꼬리 {b['wick_pct']:.1f}% "
                 f"(캔들의 {b['wick_ratio'] * 100:.0f}%) · 기준 {cfg.max_upper_wick_pct}% · "
                 f"{cfg.max_upper_wick_ratio * 100:.0f}% 이하")


def _finish(p: ClosingPick, weights: dict[str, float]) -> ClosingPick:
    p.matched = all(c.ok for c in p.checks if c.critical)
    got = sum(weights.get(c.name, 0.0) for c in p.checks if c.ok)
    total = sum(weights.get(c.name, 0.0) for c in p.checks)
    p.score = round(got / total * 100.0, 1) if total > 0 else 0.0
    return p


def _pick(code: str, info: dict, kind: int, b: dict) -> ClosingPick:
    return ClosingPick(
        code=code, name=info.get("name", code), market=info.get("market", ""), kind=kind,
        price=b["close"], chg=b["chg"], value=b["value"], vol_mult=b["vol_mult"],
        day_low=float(b["last"]["Low"]), high_gap=b["high_gap"],
    )


W1 = {"등락률": 1, "거래량 폭발": 2, "거래대금": 1, "신고가 근접": 2,
      "윗꼬리 없는 양봉": 2, "이평 정배열": 1, "추격 아님": 1}


def detect_type1(df: pd.DataFrame, code: str, info: dict, cfg: ClosingConfig) -> ClosingPick:
    b = _base(df, cfg)
    p = _pick(code, info, 1, b)
    last = b["last"]
    ma5, ma20 = float(last.get("ma5", np.nan)), float(last.get("ma20", np.nan))
    g5 = gap_pct(b["close"], ma5)

    p.checks = [
        Check("등락률", cfg.t1_min_chg <= b["chg"] < cfg.t1_max_chg,
              f"{b['chg']:+.1f}% (기준 +{cfg.t1_min_chg:.0f}% 이상, 상한가 잠김 제외)"),
        Check("거래량 폭발", np.isfinite(b["vol_mult"]) and b["vol_mult"] >= cfg.t1_vol_mult,
              f"직전 20일 평균의 {b['vol_mult']:.1f}배 (기준 {cfg.t1_vol_mult:.0f}배)"),
        Check("거래대금", b["value"] >= cfg.t1_min_value,
              f"{b['value'] / 1e8:,.0f}억 (기준 {cfg.t1_min_value / 1e8:,.0f}억)"),
        Check("신고가 근접", b["high_gap"] >= -cfg.t1_max_high_gap,
              ("신고가 경신" if b["high_gap"] >= 0 else f"전고점 대비 {b['high_gap']:+.1f}%")
              + f" (기준 -{cfg.t1_max_high_gap:.0f}% 이내)"),
        _candle_check(b, cfg),
        Check("이평 정배열", b["close"] > ma5 > ma20,
              f"종가 > 5일선 {ma5:,.0f} > 20일선 {ma20:,.0f}", critical=False),
        Check("추격 아님", np.isfinite(g5) and g5 <= cfg.t1_max_ma5_gap,
              f"5일선 이격 {g5:+.1f}% (기준 {cfg.t1_max_ma5_gap:.0f}% 이하)", critical=False),
    ]
    return _finish(p, W1)


W2 = {"직전 신고가": 2, "기간 조정": 1, "저점 상승": 2, "이평 눌림 반등": 2,
      "우상향 전환": 1, "거래량 증가": 1, "윗꼬리 없는 양봉": 2, "전고점 이격": 1}


def detect_type2(df: pd.DataFrame, code: str, info: dict, cfg: ClosingConfig) -> ClosingPick:
    b = _base(df, cfg)
    p = _pick(code, info, 2, b)
    n = len(df)
    last = b["last"]
    high = df["High"].astype(float)

    # 최근 t2_peak_within 봉 안에서, 그 시점 기준 high_lookback 신고가를 찍은 가장 높은 봉
    roll_max = high.rolling(cfg.high_lookback, min_periods=20).max()
    lo_i = max(20, n - 1 - cfg.t2_peak_within)
    hi_i = n - 1 - cfg.t2_min_rest            # 조정 봉을 남겨야 한다
    peaks = [i for i in range(lo_i, hi_i + 1) if high.iloc[i] >= roll_max.iloc[i]]
    peak_i = max(peaks, key=lambda i: high.iloc[i]) if peaks else None

    if peak_i is None:
        p.checks = [Check("직전 신고가", False,
                          f"최근 {cfg.t2_peak_within}봉 안에 {cfg.high_lookback}일 신고가 없음")]
        return _finish(p, W2)

    peak = float(high.iloc[peak_i])
    rest = df.iloc[peak_i + 1:n - 1]           # 신고가 다음 봉 ~ 어제
    bars = len(rest)
    depth = (1.0 - float(rest["Low"].min()) / peak) * 100.0 if bars else np.nan
    half = bars // 2
    first_low = float(rest["Low"].iloc[:half].min()) if half else np.nan
    second_low = float(rest["Low"].iloc[half:].min()) if half else np.nan

    recent = df.iloc[-3:]
    touched = []
    for col, lab in (("ma5", "5일선"), ("ma20", "20일선")):
        ma = recent[col].astype(float)
        if ((recent["Low"].astype(float) <= ma * (1 + cfg.t2_touch_tol / 100.0)) & ma.notna()).any():
            touched.append(lab)
    ma5, ma20 = float(last["ma5"]), float(last["ma20"])
    ma5_prev = float(df["ma5"].iloc[-2])
    peak_gap = gap_pct(b["close"], peak)

    p.high_gap = peak_gap
    p.checks = [
        Check("직전 신고가", True,
              f"{df.index[peak_i].date()} 고가 {peak:,.0f}원 ({cfg.high_lookback}일 신고가)"),
        Check("기간 조정", bars >= cfg.t2_min_rest and depth <= cfg.t2_max_depth,
              f"{bars}봉 쉬었고 고점 대비 최대 -{depth:.1f}% "
              f"(기준 {cfg.t2_min_rest}봉 이상 · -{cfg.t2_max_depth:.0f}% 이내)"),
        Check("저점 상승", half > 0 and second_low > first_low,
              f"조정 전반 저점 {first_low:,.0f} → 후반 {second_low:,.0f}"),
        Check("이평 눌림 반등", bool(touched) and b["close"] >= ma5 and b["close"] >= ma20,
              (f"최근 3봉 {'/'.join(touched)} 눌림" if touched else "최근 3봉 이평 눌림 없음")
              + f" · 종가 5일선 {gap_pct(b['close'], ma5):+.1f}% / 20일선 {gap_pct(b['close'], ma20):+.1f}%"),
        Check("우상향 전환", b["chg"] >= cfg.t2_min_chg and ma5 >= ma5_prev,
              f"등락 {b['chg']:+.1f}% (기준 +{cfg.t2_min_chg:.0f}%) · 5일선 "
              f"{'상승' if ma5 >= ma5_prev else '하락'}"),
        Check("거래량 증가",
              np.isfinite(b["vol_mult"]) and b["vol_mult"] >= cfg.t2_vol_mult
              and b["value"] >= cfg.t2_min_value,
              f"직전 20일 평균의 {b['vol_mult']:.1f}배 · {b['value'] / 1e8:,.0f}억 "
              f"(기준 {cfg.t2_vol_mult}배 · {cfg.t2_min_value / 1e8:,.0f}억)"),
        _candle_check(b, cfg),
        Check("전고점 이격", peak_gap >= -cfg.t2_max_high_gap,
              f"신고가 고점 대비 {peak_gap:+.1f}% (기준 -{cfg.t2_max_high_gap:.0f}% 이내)"),
    ]
    return _finish(p, W2)


def _liquid(df: pd.DataFrame, cfg: Config) -> bool:
    return float(df["Close"].iloc[-1]) >= cfg.min_price and len(df) >= 60


def scan(frames: dict[str, pd.DataFrame], meta: dict[str, dict], cfg: Config) -> list[ClosingPick]:
    """성립한 종가배팅 후보. 한 종목이 두 유형에 다 걸리면 점수 높은 쪽만 남긴다."""
    out: list[ClosingPick] = []
    for code, df in frames.items():
        if not _liquid(df, cfg):
            continue
        # 거래정지일은 시가·고가·저가가 0으로 들어온다
        df = df[(df["High"] > 0) & (df["Low"] > 0) & (df["Open"] > 0)]
        if len(df) < 60:
            continue
        info = meta.get(code, {})
        hits =[r for r in (detect_type1(df, code, info, cfg.closing),
                            detect_type2(df, code, info, cfg.closing)) if r.matched]
        if hits:
            out.append(max(hits, key=lambda r: (r.score, -r.kind)))
    out.sort(key=lambda r: (r.score, r.value), reverse=True)
    return out


def rank(picks: list[ClosingPick], cfg: ClosingConfig) -> list[ClosingPick]:
    """enrich 이후 — 재료 없는 종목을 빼고, 소형주·양매수에 가점을 준다."""
    keep = [p for p in picks if not (cfg.require_news and p.news_checked and not p.news)]

    def key(p: ClosingPick):
        bonus = 0.0
        if np.isfinite(p.marcap) and p.marcap <= cfg.small_cap:
            bonus += 5
        if np.isfinite(p.foreign) and np.isfinite(p.organ) and p.foreign > 0 and p.organ > 0:
            bonus += 5
        return (p.score + bonus, p.value)

    return sorted(keep, key=key, reverse=True)


def to_dataframe(picks: list[ClosingPick]) -> pd.DataFrame:
    rows = []
    for p in picks:
        rows.append({
            "종목코드": p.code,
            "종목명": p.name,
            "시장": p.market,
            "유형": f"{p.kind} {p.label}",
            "점수": p.score,
            "현재가": round(p.price),
            "등락률": round(p.chg, 2),
            "거래대금(억)": round(p.value / 1e8, 1),
            "거래량배수": round(p.vol_mult, 1) if np.isfinite(p.vol_mult) else None,
            "전고점이격": round(p.high_gap, 1) if np.isfinite(p.high_gap) else None,
            "손절(당일저가)": round(p.day_low),
            "시총(억)": round(p.marcap / 1e8) if np.isfinite(p.marcap) else None,
            "외인전일": p.foreign if np.isfinite(p.foreign) else None,
            "기관전일": p.organ if np.isfinite(p.organ) else None,
            "재료": p.news[0][1] if p.news else ("뉴스 없음" if p.news_checked else "조회 실패"),
            "감점": p.note,
        })
    return pd.DataFrame(rows)
