"""VCP(Volatility Contraction Pattern) 탐지.

미너비니의 VCP는 Stage 2 상승 이후 만들어지는 베이스로,
조정 폭이 단계적으로 좁아지고(예: 20% → 10% → 5%) 거래량이 말라붙은 뒤
마지막 수축의 고점(피벗)을 대량 거래로 돌파하는 형태다.

여기서는 이 정성적 패턴을 다음 숫자들로 옮긴다.
  - 수축 횟수 (2~6회)
  - 각 수축의 깊이가 직전 대비 축소되는지
  - 마지막 수축의 깊이 (타이트할수록 좋음)
  - 거래량 마름 (최근 5일 평균 / 50일 평균)
  - 피벗까지의 거리와 손절폭
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import VCPConfig
from .indicators import atr_pct, true_price_tightness


@dataclass
class Contraction:
    high_idx: int
    low_idx: int
    high: float
    low: float
    depth: float  # %


@dataclass
class VCPResult:
    is_vcp: bool = False
    status: str = "-"          # 형성중 / 매수구간 / 돌파 / -
    contractions: list = field(default_factory=list)
    n_contractions: int = 0
    depths: list = field(default_factory=list)
    base_bars: int = 0
    base_high: float = np.nan
    pivot: float = np.nan
    stop: float = np.nan
    distance_to_pivot: float = np.nan  # % (양수면 아직 피벗 아래)
    stop_pct: float = np.nan           # 현재가 대비 손절폭 %
    risk_from_pivot: float = np.nan    # 피벗에서 진입했을 때의 손절폭 %
    dryup_ratio: float = np.nan
    tightness: float = np.nan
    atr_contraction: float = np.nan    # 최근 ATR% / 베이스 초기 ATR%
    breakout_volume_mult: float = np.nan   # 돌파가 일어난 '그 날'의 거래량 / 50일 평균
    volume_mult_today: float = np.nan      # 오늘 봉의 거래량 / 50일 평균
    score: float = 0.0
    score_kind: str = "setup"   # "setup" | "breakout" — 어느 점수 체계로 매겼는지
    note: str = ""
    base_start_date = None     # 베이스(첫 수축의 고점)가 시작된 날
    pivot_date = None          # 피벗(마지막 수축의 고점)이 만들어진 날
    low_date = None            # 마지막 수축의 저점 날짜
    breakout_date = None       # 피벗을 처음 넘어선 날


def _swing_points(df: pd.DataFrame, order: int) -> list[tuple[int, str, float]]:
    """프랙탈 방식 스윙 고점/저점. (인덱스, 'H'|'L', 가격) 리스트를 시간순으로 반환."""
    high = df["High"].to_numpy(dtype=float)
    low = df["Low"].to_numpy(dtype=float)
    n = len(high)
    pts: list[tuple[int, str, float]] = []
    for i in range(order, n - order):
        window_h = high[i - order : i + order + 1]
        window_l = low[i - order : i + order + 1]
        if high[i] >= window_h.max():
            pts.append((i, "H", float(high[i])))
        elif low[i] <= window_l.min():
            pts.append((i, "L", float(low[i])))
    return pts


def _alternate(pts: list[tuple[int, str, float]]) -> list[tuple[int, str, float]]:
    """연속된 같은 종류의 스윙은 더 극단적인 것만 남겨 H/L 교대 시퀀스로 만든다."""
    out: list[tuple[int, str, float]] = []
    for p in pts:
        if not out:
            out.append(p)
            continue
        if p[1] == out[-1][1]:
            better = p[2] > out[-1][2] if p[1] == "H" else p[2] < out[-1][2]
            if better:
                out[-1] = p
        else:
            out.append(p)
    return out


def _build_contractions(seq: list[tuple[int, str, float]]) -> list[Contraction]:
    """교대 시퀀스에서 (고점 → 저점) 쌍을 수축으로 뽑는다."""
    cons: list[Contraction] = []
    for a, b in zip(seq, seq[1:]):
        if a[1] == "H" and b[1] == "L" and a[2] > 0:
            depth = (a[2] - b[2]) / a[2] * 100.0
            if depth > 0:
                cons.append(Contraction(a[0], b[0], a[2], b[2], depth))
    return cons


def _longest_shrinking_suffix(cons: list[Contraction], ratio: float, max_n: int) -> list[Contraction]:
    """뒤에서부터 '직전보다 좁아지는' 조건이 유지되는 가장 긴 구간을 찾는다."""
    if not cons:
        return []
    tail = cons[-max_n:]
    best = [tail[-1]]
    for i in range(len(tail) - 2, -1, -1):
        # tail[i]가 tail[i+1]보다 깊어야(=더 큰 조정) 수축 흐름이 이어진다
        if best[0].depth <= tail[i].depth * ratio:
            best.insert(0, tail[i])
        else:
            break
    return best


def detect(df: pd.DataFrame, cfg: VCPConfig) -> VCPResult:
    """일봉 DataFrame에서 VCP를 탐지한다."""
    res = VCPResult()
    if len(df) < max(cfg.lookback, 60):
        res.note = "데이터 부족"
        return res

    dates = df.index[-cfg.lookback :]
    window = df.iloc[-cfg.lookback :].copy().reset_index(drop=True)
    close = float(window["Close"].iloc[-1])

    seq = _alternate(_swing_points(window, cfg.swing_order))
    # 마지막 스윙이 고점이면, 그 이후 최저가를 임시 저점으로 붙여 진행 중인 수축도 잡는다
    if seq and seq[-1][1] == "H":
        tail_start = seq[-1][0]
        tail = window["Low"].iloc[tail_start + 1 :]
        if len(tail) >= 2:
            j = int(tail.idxmin())
            seq.append((j, "L", float(window["Low"].iloc[j])))

    cons_all = _build_contractions(seq)
    if not cons_all:
        res.note = "수축 구조 없음"
        return res

    cons = _longest_shrinking_suffix(cons_all, cfg.contraction_ratio, cfg.max_contractions)
    res.contractions = cons
    res.n_contractions = len(cons)
    res.depths = [round(c.depth, 1) for c in cons]

    base_start = cons[0].high_idx
    res.base_bars = len(window) - base_start
    res.base_high = float(window["High"].iloc[base_start:].max())
    res.base_start_date = dates[base_start]

    last = cons[-1]
    res.pivot = float(last.high)
    res.stop = float(last.low)
    res.pivot_date = dates[last.high_idx]
    res.low_date = dates[last.low_idx]
    res.distance_to_pivot = (res.pivot / close - 1.0) * 100.0
    res.stop_pct = (close - res.stop) / close * 100.0 if close > 0 else np.nan
    res.risk_from_pivot = (res.pivot - res.stop) / res.pivot * 100.0 if res.pivot > 0 else np.nan

    # 거래량 마름: 최근 5일 평균 대비 50일 평균
    vol = df["Volume"].astype(float)
    vol50s = vol.rolling(50, min_periods=20).mean()
    vol50 = float(vol50s.iloc[-1])
    res.dryup_ratio = float(vol.iloc[-5:].mean()) / vol50 if vol50 > 0 else np.nan
    res.volume_mult_today = float(vol.iloc[-1]) / vol50 if vol50 > 0 else np.nan

    res.tightness = true_price_tightness(df)

    ap = atr_pct(df.iloc[-cfg.lookback :])
    early = float(ap.iloc[: max(5, len(ap) // 4)].mean())
    recent = float(ap.iloc[-10:].mean())
    res.atr_contraction = recent / early if early and np.isfinite(early) and early > 0 else np.nan

    # --- 판정 ---
    fails = []
    if res.n_contractions < cfg.min_contractions:
        fails.append(f"수축 {res.n_contractions}회(<{cfg.min_contractions})")
    if res.base_bars < cfg.min_base_bars:
        fails.append(f"베이스 {res.base_bars}봉(짧음)")
    if cons[0].depth > cfg.max_first_depth:
        fails.append(f"첫 조정 {cons[0].depth:.0f}%(과대)")
    if last.depth > cfg.max_last_depth:
        fails.append(f"마지막 수축 {last.depth:.0f}%(느슨)")
    if np.isfinite(res.risk_from_pivot) and res.risk_from_pivot > cfg.max_stop_distance:
        fails.append(f"피벗~손절 {res.risk_from_pivot:.0f}%(리스크 과대)")
    if cfg.require_dryup and np.isfinite(res.dryup_ratio) and res.dryup_ratio > cfg.max_dryup_ratio:
        fails.append(f"거래량 마름 {res.dryup_ratio:.2f}(미달)")

    res.is_vcp = not fails
    res.note = "; ".join(fails)

    if close > res.pivot:
        closes = window["Close"].to_numpy(dtype=float)
        i = len(closes) - 1
        while i > 0 and closes[i - 1] > res.pivot:
            i -= 1
        res.breakout_date = dates[i]
        # 미너비니의 돌파 확인은 '돌파한 그 날'의 거래량이다. 오늘 거래량이 아니다.
        abs_i = len(df) - len(window) + i
        base_vol = float(vol50s.iloc[abs_i])
        if base_vol > 0:
            res.breakout_volume_mult = float(vol.iloc[abs_i]) / base_vol

    if res.is_vcp:
        if close > res.pivot:
            surged = np.isfinite(res.breakout_volume_mult) and res.breakout_volume_mult >= cfg.breakout_volume_mult
            res.status = "돌파" if surged else "피벗위(거래량부족)"
        elif res.distance_to_pivot <= cfg.max_distance_to_pivot:
            res.status = "매수구간"
        else:
            res.status = "형성중"

    # 돌파한 종목과 아직 돌파 전인 셋업은 좋은 조건이 서로 다르다.
    # 같은 점수로 줄 세우면 돌파 종목의 순위가 뒤집힌다 (config.py 주석 참고).
    if close > res.pivot:
        res.score_kind = "breakout"
        res.score = _breakout_score(res, cfg)
    else:
        res.score_kind = "setup"
        res.score = _setup_score(res, cfg)
    return res


def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def _breakout_score(r: VCPResult, cfg: VCPConfig) -> float:
    """이미 피벗을 넘은 종목의 점수 0~100.

    돌파의 질을 가르는 건 '돌파일 거래량'과 '피벗 위 여유'였다. 둘 다 세 시장
    구간에서 같은 방향으로 단조롭게 움직였다.
      돌파일 거래량 1배↓ → 휩소 67~72% / 3배↑ → 24~52%
      피벗 위 0.3%↓  → 휩소 75~83% / 4%↑  → 24~38%, 초과수익도 단조 증가
    """
    if not r.contractions:
        return 0.0

    # 돌파일 거래량 (50일 평균 대비). 1배를 밑돌면 0점.
    if np.isfinite(r.breakout_volume_mult):
        s_vol = cfg.bo_score_vol_pts * _clamp(
            (r.breakout_volume_mult - 1.0) / max(1e-9, cfg.bo_score_vol_cap - 1.0)
        )
    else:
        s_vol = 0.0

    # 피벗 위 여유. distance_to_pivot은 피벗이 종가보다 얼마나 위인지라, 돌파 후엔 음수다.
    above = -r.distance_to_pivot
    if np.isfinite(above):
        s_room = cfg.bo_score_room_pts * _clamp(
            (above - cfg.bo_score_room_min)
            / max(1e-9, cfg.bo_score_room_cap - cfg.bo_score_room_min)
        )
    else:
        s_room = 0.0

    # 수축 횟수 — 기존 체계와 같은 모양, 배점만 축소
    n = r.n_contractions
    unit = cfg.bo_score_count_pts / 25.0
    s_count = {0: 0.0, 1: 0.0, 2: 15.0 * unit}.get(n, (25.0 if n <= 5 else 20.0) * unit)

    # 수축 감쇠비 — 세 구간 모두 의도대로 작동한 유일한 기존 성분이라 배점을 늘렸다
    first = r.contractions[0].depth
    decay = r.contractions[-1].depth / first if first > 0 else 1.0
    s_decay = cfg.bo_score_decay_pts * _clamp(1 - decay)

    return round(s_vol + s_room + s_count + s_decay, 1)


def _setup_score(r: VCPResult, cfg: VCPConfig) -> float:
    """아직 피벗을 넘지 않은 셋업의 품질 점수 0~100.

    주의: 이 점수는 '돌파 전'을 전제로 한다. 돌파한 종목에 쓰면 거래량 마름·
    스프레드 압축·피벗 근접도가 전부 역방향으로 작동한다 (config.py 주석 참고).
    돌파 종목은 _breakout_score()를 쓴다.
    """
    if not r.contractions:
        return 0.0

    def clamp(x, lo=0.0, hi=1.0):
        return max(lo, min(hi, x))

    # 수축 횟수 (25점): 3~4회가 이상적
    n = r.n_contractions
    s_count = {0: 0, 1: 0, 2: 15}.get(n, 25 if n <= 5 else 20)

    # 마지막 수축의 타이트함 (20점)
    s_tight = 20 * clamp(1 - r.contractions[-1].depth / cfg.max_last_depth)

    # 수축 감쇠비 = 마지막/첫번째 (15점)
    decay = r.contractions[-1].depth / r.contractions[0].depth if r.contractions[0].depth > 0 else 1.0
    s_decay = 15 * clamp(1 - decay)

    # 거래량 마름 (20점)
    s_vol = 20 * clamp((cfg.max_dryup_ratio - r.dryup_ratio) / cfg.max_dryup_ratio) if np.isfinite(r.dryup_ratio) else 0

    # 가격 스프레드 압축 (10점): 백분위 20 이하면 만점
    s_spread = 10 * clamp((50 - r.tightness) / 50) if np.isfinite(r.tightness) else 0

    # 피벗 근접도 (10점)
    d = r.distance_to_pivot
    if np.isfinite(d):
        s_dist = 10 * clamp(1 - abs(d) / cfg.max_distance_to_pivot)
    else:
        s_dist = 0

    return round(s_count + s_tight + s_decay + s_vol + s_spread + s_dist, 1)


# 예전 이름으로 부르던 곳이 있을 수 있어 별칭을 남긴다
_score = _setup_score
