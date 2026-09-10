"""상한가 이후 3대 일봉 조정 패턴 판정 (A / B / C).

  패턴 A  2~3일 단기 조정 — 장대양봉 상단에서 거래량 줄며 도지, 5일선 사수
  패턴 B  일주일 조정 (4음 1양) — 5일선을 깨고 내리다 20일선에서 양봉 지지
  패턴 C  2주 기간 조정 (8~12봉) — 거래량 극감 후 재증가하며 5일선 복귀 양봉

셋 다 "매수는 패턴이 완성되는 날의 종가"라는 점이 같고,
손절선만 다르다 (A는 5일선, B/C는 20일선).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import Config, PatternAConfig, PatternBConfig, PatternCConfig
from .indicators import body_pct, body_ratio, clamp, gap_pct, is_bear, is_bull, range_pct
from .triggers import Trigger

PATTERN_LABEL = {
    "A": "2~3일 단기조정 (상단 도지)",
    "B": "일주일 조정 (4음 1양)",
    "C": "2주 기간조정 (8~12봉)",
}


@dataclass
class Check:
    """개별 조건 하나의 판정 결과 — 리포트에 그대로 찍는다."""

    name: str
    ok: bool
    detail: str
    critical: bool = True     # False면 실패해도 패턴 자체는 살려둔다(감점만)


@dataclass
class PatternResult:
    code: str = ""                     # A / B / C
    matched: bool = False
    trigger: Trigger | None = None
    bars_since_trigger: int = 0
    buy_price: float = np.nan          # 오늘 종가 = 매수 타점
    stop_price: float = np.nan         # 손절선 (이동평균)
    stop_label: str = ""               # "5일선" / "20일선"
    stop_pct: float = np.nan           # 매수가 대비 손절폭 %
    vol_ratio: float = np.nan          # 조정 구간 평균 거래량 / 트리거일 거래량
    depth: float = np.nan              # 트리거 종가 대비 조정 저점 하락률 %
    score: float = 0.0
    checks: list = field(default_factory=list)

    @property
    def label(self) -> str:
        return PATTERN_LABEL.get(self.code, self.code)

    @property
    def failed(self) -> list[str]:
        return [c.name for c in self.checks if not c.ok]

    @property
    def note(self) -> str:
        return "; ".join(f"{c.name} {c.detail}" for c in self.checks if not c.ok)


def _finalize(res: PatternResult, weights: dict[str, float]) -> PatternResult:
    """critical 조건이 전부 통과했는지 확인하고 점수를 매긴다."""
    res.matched = all(c.ok for c in res.checks if c.critical)
    got = sum(weights.get(c.name, 0.0) for c in res.checks if c.ok)
    total = sum(weights.get(c.name, 0.0) for c in res.checks)
    res.score = round(got / total * 100.0, 1) if total > 0 else 0.0
    return res


def _seg_stats(df: pd.DataFrame, trig: Trigger, upto: int) -> tuple[pd.DataFrame, float, float]:
    """조정 구간(트리거 다음 봉 ~ upto)과 거래량비·조정 깊이."""
    seg = df.iloc[trig.idx + 1: upto + 1]
    vol_ratio = float(seg["Volume"].mean()) / trig.volume if trig.volume > 0 else np.nan
    low = float(seg["Low"].min()) if len(seg) else np.nan
    depth = (trig.close - low) / trig.close * 100.0 if trig.close > 0 else np.nan
    return seg, vol_ratio, depth


def _bear_flags(df: pd.DataFrame, seg: pd.DataFrame, trig: Trigger, real: bool) -> list[bool]:
    """조정 구간 각 봉이 음봉인가.

    real 이면 '종가 < 시가'만으로는 부족하고 전일 종가보다도 낮아야 한다.
    갭상승 후 음봉으로 마감했지만 전일보다 오른 날은 하락으로 치지 않는다.
    """
    out = []
    for k in range(len(seg)):
        row = seg.iloc[k]
        bear = is_bear(row)
        if real:
            prev = float(df["Close"].iloc[trig.idx + k])   # 직전 봉 (k=0이면 트리거일)
            bear = bear and float(row["Close"]) < prev
        out.append(bool(bear))
    return out


def _longest_run(flags: list[bool]) -> int:
    """연속으로 True인 최대 길이."""
    best = run = 0
    for f in flags:
        run = run + 1 if f else 0
        best = max(best, run)
    return best


# ────────────────────────────── 패턴 A ──────────────────────────────

_A_WEIGHTS = {
    "조정 기간": 15.0,
    "5일선 사수": 25.0,
    "거래량 감소": 25.0,
    "고점 5% 이내": 20.0,
    "도지 캔들": 15.0,
}


def detect_a(df: pd.DataFrame, trig: Trigger, cfg: PatternAConfig) -> PatternResult:
    """장대양봉 상단에서 2~3일 짧게 쉬어가는 형태."""
    res = PatternResult(code="A", trigger=trig)
    i = len(df) - 1
    d = i - trig.idx
    res.bars_since_trigger = d
    today = df.iloc[i]

    res.checks.append(Check(
        "조정 기간", cfg.min_bars <= d <= cfg.max_bars,
        f"{d}일 (기준 {cfg.min_bars}~{cfg.max_bars}일)",
    ))
    if not (cfg.min_bars <= d <= cfg.max_bars):
        return _finalize(res, _A_WEIGHTS)

    seg, vol_ratio, depth = _seg_stats(df, trig, i)
    res.vol_ratio, res.depth = vol_ratio, depth

    # 5일선 사수 — 조정 구간 모든 종가가 5일선(허용치 이내) 위
    ma_col = f"ma{cfg.ma_support}"
    ma_now = float(today.get(ma_col, np.nan))
    floor = seg[ma_col] * (1.0 - cfg.ma_tolerance / 100.0)
    held = bool((seg["Close"] >= floor).all()) and np.isfinite(ma_now)
    worst = float((seg["Close"] / seg[ma_col] - 1.0).min() * 100.0) if np.isfinite(ma_now) else np.nan
    res.checks.append(Check(
        "5일선 사수", held, f"최저 이격 {worst:+.1f}% (허용 -{cfg.ma_tolerance:.0f}%)",
    ))

    # 거래량 감소 — 조정 평균이 트리거일 대비 충분히 줄고, 뒤로 갈수록 더 줄어야 한다
    vol_ok = np.isfinite(vol_ratio) and vol_ratio <= cfg.max_vol_vs_trigger
    monotonic = True
    if cfg.require_vol_decline and len(seg) >= 2:
        v = seg["Volume"].to_numpy(dtype=float)
        monotonic = bool((v[1:] <= v[:-1]).all())
        vol_ok = vol_ok and monotonic
    res.checks.append(Check(
        "거래량 감소", bool(vol_ok),
        f"트리거일 대비 {vol_ratio * 100:.0f}% (기준 {cfg.max_vol_vs_trigger * 100:.0f}% 이하)"
        + ("" if monotonic else " · 조정 중 거래량이 다시 늘어난 날이 있음"),
    ))

    # 장대양봉 상단 5% 이내에서 버티는가
    drop = (trig.high - float(today["Close"])) / trig.high * 100.0 if trig.high > 0 else np.nan
    res.checks.append(Check(
        "고점 5% 이내", bool(np.isfinite(drop) and drop <= cfg.max_drop_from_high),
        f"장대양봉 고가 대비 {-drop:+.1f}% (기준 -{cfg.max_drop_from_high:.0f}% 이내)",
    ))

    # 오늘 캔들이 도지(짧은 캔들)인가 — 몸통도 작고 캔들 자체도 트리거 대비 짧아야 한다
    body = body_pct(today)
    trig_range = trig.high - trig.low
    today_range = float(today["High"]) - float(today["Low"])
    ratio = today_range / trig_range if trig_range > 0 else np.nan
    doji_ok = (
        np.isfinite(body) and abs(body) <= cfg.max_doji_body
        and np.isfinite(ratio) and ratio <= cfg.max_range_ratio
    )
    res.checks.append(Check(
        "도지 캔들", bool(doji_ok),
        f"몸통 {body:+.1f}% · 길이 트리거의 {ratio * 100:.0f}%"
        f" (기준 |{cfg.max_doji_body:.1f}|% · {cfg.max_range_ratio * 100:.0f}%)",
    ))

    res.buy_price = float(today["Close"])
    res.stop_price = ma_now
    res.stop_label = f"{cfg.ma_support}일선"
    res.stop_pct = (res.buy_price - ma_now) / res.buy_price * 100.0 if res.buy_price > 0 else np.nan
    return _finalize(res, _A_WEIGHTS)


# ────────────────────────────── 패턴 B ──────────────────────────────

_B_WEIGHTS = {
    "조정 기간": 10.0,
    "음봉 개수": 20.0,
    "5일선 이탈": 10.0,
    "거래량 급감": 25.0,
    "20일선 지지 양봉": 25.0,
    "저점 방어": 10.0,
}


def detect_b(df: pd.DataFrame, trig: Trigger, cfg: PatternBConfig) -> PatternResult:
    """4개의 음봉으로 5일선을 깨고 내려온 뒤, 20일선에서 양봉으로 되돌리는 형태."""
    res = PatternResult(code="B", trigger=trig)
    i = len(df) - 1
    d = i - trig.idx
    res.bars_since_trigger = d
    today = df.iloc[i]

    res.checks.append(Check(
        "조정 기간", cfg.min_bars <= d <= cfg.max_bars,
        f"{d}일 (기준 {cfg.min_bars}~{cfg.max_bars}일)",
    ))
    if not (cfg.min_bars <= d <= cfg.max_bars):
        return _finalize(res, _B_WEIGHTS)

    down = df.iloc[trig.idx + 1: i]          # 오늘(양봉) 직전까지가 하락 구간
    seg, vol_ratio, depth = _seg_stats(df, trig, i)
    res.vol_ratio, res.depth = vol_ratio, depth

    flags = _bear_flags(df, down, trig, cfg.require_real_decline)
    n_bear = int(sum(flags))
    run = _longest_run(flags)
    need = cfg.min_down_bars
    bear_ok = (run >= need) if cfg.require_consecutive else (n_bear >= need)
    kind = "연속 음봉" if cfg.require_consecutive else "음봉"
    detail = f"{len(down)}봉 중 음봉 {n_bear}개"
    if cfg.require_consecutive:
        detail += f" · 최장 연속 {run}개"
    if cfg.require_real_decline:
        detail += " (종가가 전일보다 낮은 봉만 인정)"
    res.checks.append(Check(
        "음봉 개수", bool(bear_ok), f"{detail} — 기준 {kind} {need}개 이상",
    ))

    broke = bool((down["Close"] < down["ma5"]).any()) if len(down) else False
    res.checks.append(Check(
        "5일선 이탈", broke or not cfg.require_ma5_break,
        "이탈함" if broke else "5일선 위 유지 (패턴 A 쪽에 가까움)",
        critical=cfg.require_ma5_break,
    ))

    vol_ok = np.isfinite(vol_ratio) and vol_ratio <= cfg.max_vol_vs_trigger
    res.checks.append(Check(
        "거래량 급감", bool(vol_ok),
        f"트리거일 대비 {vol_ratio * 100:.0f}% (기준 {cfg.max_vol_vs_trigger * 100:.0f}% 이하)",
    ))

    # 오늘: 20일선 근처/위에서 지지받는 양봉
    ma_col = f"ma{cfg.ma_support}"
    ma_now = float(today.get(ma_col, np.nan))
    close = float(today["Close"])
    support_ok = (
        is_bull(today)
        and np.isfinite(ma_now)
        and close >= ma_now * (1.0 - cfg.ma_tolerance / 100.0)
    )
    res.checks.append(Check(
        "20일선 지지 양봉", bool(support_ok),
        f"{'양봉' if is_bull(today) else '음봉'} · 20일선 이격 {gap_pct(close, ma_now):+.1f}%",
    ))

    # 조정 저점이 20일선을 크게 깨지 않았는가
    low = float(seg["Low"].min()) if len(seg) else np.nan
    undercut = (1.0 - low / ma_now) * 100.0 if np.isfinite(ma_now) and ma_now > 0 else np.nan
    res.checks.append(Check(
        "저점 방어", bool(np.isfinite(undercut) and undercut <= cfg.max_ma_undercut),
        f"저점이 20일선 아래 {max(undercut, 0):.1f}% (허용 {cfg.max_ma_undercut:.0f}%)",
    ))

    res.buy_price = close
    res.stop_price = ma_now
    res.stop_label = f"{cfg.ma_support}일선"
    res.stop_pct = (close - ma_now) / close * 100.0 if close > 0 else np.nan
    return _finalize(res, _B_WEIGHTS)


# ────────────────────────────── 패턴 C ──────────────────────────────

_C_WEIGHTS = {
    "조정 기간": 10.0,
    "거래량 극감": 25.0,
    "거래량 재증가": 20.0,
    "5일선 복귀 양봉": 25.0,
    "20일선 방어": 20.0,
}


def detect_c(df: pd.DataFrame, trig: Trigger, cfg: PatternCConfig) -> PatternResult:
    """2주 동안 거래량이 말라붙으며 옆으로 기다가, 거래량과 함께 5일선을 되찾는 형태."""
    res = PatternResult(code="C", trigger=trig)
    i = len(df) - 1
    d = i - trig.idx
    res.bars_since_trigger = d
    today = df.iloc[i]

    res.checks.append(Check(
        "조정 기간", cfg.min_bars <= d <= cfg.max_bars,
        f"{d}봉 (기준 {cfg.min_bars}~{cfg.max_bars}봉)",
    ))
    if not (cfg.min_bars <= d <= cfg.max_bars):
        return _finalize(res, _C_WEIGHTS)

    seg, vol_ratio, depth = _seg_stats(df, trig, i)
    res.vol_ratio, res.depth = vol_ratio, depth
    base = df.iloc[trig.idx + 1: i]          # 오늘을 뺀 조정 구간

    # 거래량이 극도로 줄어든 구간이 있었는가
    dryup = float((base["vol5"] / base["vol20"]).min()) if len(base) else np.nan
    dry_ok = (
        np.isfinite(vol_ratio) and vol_ratio <= cfg.max_vol_vs_trigger
        and np.isfinite(dryup) and dryup <= cfg.max_dryup_ratio
    )
    res.checks.append(Check(
        "거래량 극감", bool(dry_ok),
        f"트리거일 대비 {vol_ratio * 100:.0f}% · 5/20일 거래량비 최저 {dryup:.2f}"
        f" (기준 {cfg.max_vol_vs_trigger * 100:.0f}% · {cfg.max_dryup_ratio:.2f})",
    ))

    # 오늘 거래량이 다시 붙었는가
    base_vol = float(base["Volume"].mean()) if len(base) else np.nan
    revive = float(today["Volume"]) / base_vol if np.isfinite(base_vol) and base_vol > 0 else np.nan
    res.checks.append(Check(
        "거래량 재증가", bool(np.isfinite(revive) and revive >= cfg.vol_revive_mult),
        f"조정 평균의 {revive:.1f}배 (기준 {cfg.vol_revive_mult:.1f}배 이상)",
    ))

    # 5일선 위로 올라타는 양봉 — 어제는 아래, 오늘은 위
    ma_col = f"ma{cfg.ma_reclaim}"
    close = float(today["Close"])
    ma_now = float(today.get(ma_col, np.nan))
    prev = df.iloc[i - 1]
    was_below = float(prev["Close"]) < float(prev.get(ma_col, np.nan))
    reclaim_ok = is_bull(today) and np.isfinite(ma_now) and close > ma_now and was_below
    res.checks.append(Check(
        "5일선 복귀 양봉", bool(reclaim_ok),
        f"{'양봉' if is_bull(today) else '음봉'} · 5일선 이격 {gap_pct(close, ma_now):+.1f}%"
        f" · 전일 5일선 {'아래' if was_below else '위(이미 복귀)'}",
    ))

    # 조정 내내 20일선을 크게 이탈하지 않았는가
    hold_col = f"ma{cfg.ma_hold}"
    ma_hold = float(today.get(hold_col, np.nan))
    low = float(seg["Low"].min()) if len(seg) else np.nan
    undercut = (1.0 - low / ma_hold) * 100.0 if np.isfinite(ma_hold) and ma_hold > 0 else np.nan
    hold_ok = (
        np.isfinite(undercut) and undercut <= cfg.max_ma_undercut
        and np.isfinite(depth) and depth <= cfg.max_depth
    )
    res.checks.append(Check(
        "20일선 방어", bool(hold_ok),
        f"저점이 20일선 아래 {max(undercut, 0):.1f}% · 조정 깊이 {depth:.1f}%"
        f" (허용 {cfg.max_ma_undercut:.0f}% · {cfg.max_depth:.0f}%)",
    ))

    res.buy_price = close
    res.stop_price = ma_hold
    res.stop_label = f"{cfg.ma_hold}일선"
    res.stop_pct = (close - ma_hold) / close * 100.0 if close > 0 else np.nan
    return _finalize(res, _C_WEIGHTS)


# ────────────────────────────── 통합 ──────────────────────────────

DETECTORS = {"A": (detect_a, "pattern_a"), "B": (detect_b, "pattern_b"), "C": (detect_c, "pattern_c")}


def detect_all(df: pd.DataFrame, triggers: list[Trigger], cfg: Config) -> list[PatternResult]:
    """트리거 × 패턴을 전부 돌려보고 성립한 것만 점수 높은 순으로 반환."""
    out: list[PatternResult] = []
    for trig in triggers:
        for code, (fn, attr) in DETECTORS.items():
            res = fn(df, trig, getattr(cfg, attr))
            if res.matched:
                out.append(res)
    # 점수가 같으면 더 최근 트리거를 쓴다. 조정 중에 새 장대양봉이 나오면
    # 조정은 거기서 다시 시작되는 것이지, 예전 트리거가 이어지는 게 아니다.
    out.sort(key=lambda r: (r.score, r.trigger.idx, r.trigger.strength), reverse=True)
    return out


def best_near_miss(df: pd.DataFrame, triggers: list[Trigger], cfg: Config) -> PatternResult | None:
    """성립하진 않았지만 가장 근접한 후보 — '왜 안 되는지'를 보여주기 위해."""
    best = None
    for trig in triggers:
        for code, (fn, attr) in DETECTORS.items():
            res = fn(df, trig, getattr(cfg, attr))
            if res.matched:
                continue
            if len(res.checks) < 2:      # 기간조차 안 맞는 건 후보로 안 본다
                continue
            if best is None or (res.score, res.trigger.idx) > (best.score, best.trigger.idx):
                best = res
    return best
