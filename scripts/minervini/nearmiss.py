"""'한 끗 차이' — 관문을 하나만 놓친 종목을, 무엇을 놓쳤는지와 함께 보여준다.

스크리너가 아무것도 안 잡는 날이 이어지면 두 가지 중 하나다.
시장에 정말 자리가 없거나, 내 기준이 지나치게 좁거나. 통과한 것만 보면 둘을 구분할 수 없다.
그래서 각 종목이 어느 관문에서 걸렸는지를 그대로 드러낸다.

관문은 파이프라인 순서 그대로 다섯 개다.
  추세 → VCP → 타점 → 실적 → 매집
앞의 셋은 미너비니의 하드 기준(Trend Template · VCP · 피벗)이고,
뒤의 둘은 기술적 타점 전에 확인하는 것들이다(Code 33 · U/D 매집).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .config import Config

GATE_LABELS = ["추세", "VCP", "타점", "실적", "매집"]


@dataclass
class Gate:
    key: str
    label: str
    ok: bool | None      # None = 데이터가 없어 판정 불가 (실패로 세지 않는다)
    detail: str = ""     # 통과했을 때의 근거
    miss: str = ""       # 놓쳤을 때의 이유 (한 줄)


@dataclass
class Row:
    cand: object
    gates: list = field(default_factory=list)

    @property
    def failed(self) -> list:
        return [g for g in self.gates if g.ok is False]

    @property
    def n_fail(self) -> int:
        return len(self.failed)

    @property
    def n_ok(self) -> int:
        return sum(1 for g in self.gates if g.ok is True)

    @property
    def unknown(self) -> int:
        return sum(1 for g in self.gates if g.ok is None)

    def summary(self) -> str:
        """'모자란 것' 열에 들어갈 한 줄."""
        return " · ".join(f"{g.label} {g.miss}" for g in self.failed) or "-"


def _trend_gate(c, cfg: Config) -> Gate:
    tr = c.trend
    if tr is None:
        return Gate("trend", "추세", None, "판정 불가", "데이터 없음")
    n = tr.passed
    if tr.ok:
        return Gate("trend", "추세", True, f"{n}/8")
    # reason 은 '1. 주가 > ...' 형태라 앞 번호를 떼고 짧게 쓴다
    short = " · ".join(s.split(". ", 1)[-1] for s in tr.reason.split("; ") if s)
    if not tr.checks[7]:
        short = f"RS {c.rs_rating:.0f} (기준 {cfg.trend.min_rs_rating:.0f})" if np.isfinite(c.rs_rating) else "RS 미상"
    return Gate("trend", "추세", False, f"{n}/8", f"{n}/8 — {short}")


def _vcp_gate(c, cfg: Config) -> Gate:
    v = c.vcp
    if v.is_vcp:
        shape = " → ".join(f"{d:.0f}%" for d in v.depths) if v.depths else "확인"
        return Gate("vcp", "VCP", True, shape)
    return Gate("vcp", "VCP", False, "-", v.note or "패턴 없음")


def _timing_gate(c, cfg: Config) -> Gate:
    """피벗을 기준으로 '지금 사도 되는 자리인가'."""
    v, vc = c.vcp, cfg.vcp
    if not v.is_vcp:
        # 피벗이 없으면 타점을 판정할 근거 자체가 없다. 여기서 '실패'로 세면
        # VCP를 놓친 종목이 전부 두 개를 놓친 셈이 되어 '한 끗 차이'에서 사라진다.
        return Gate("timing", "타점", None, "피벗 미확정", "")
    if c.extended:
        return Gate("timing", "타점", False, "-", f"연장 — {c.extended_reason}")
    if v.status == "돌파":
        age = "오늘" if c.days_past_pivot == 0 else f"{c.days_past_pivot}일째"
        return Gate("timing", "타점", True, f"돌파 {age}")
    if v.status == "매수구간":
        return Gate("timing", "타점", True, f"피벗까지 {v.distance_to_pivot:+.1f}%")
    if v.status == "피벗위(거래량부족)":
        m = v.breakout_volume_mult
        got = f"{m:.1f}배" if np.isfinite(m) else "미상"
        return Gate("timing", "타점", False, "-",
                    f"돌파 거래량 {got} (기준 {vc.breakout_volume_mult:.1f}배)")
    d = v.distance_to_pivot
    far = f"피벗까지 {d:+.1f}% (기준 {vc.max_distance_to_pivot:.0f}% 이내)" if np.isfinite(d) else "피벗 거리 미상"
    return Gate("timing", "타점", False, "-", far)


def _fund_gate(c, cfg: Config) -> Gate:
    f = cfg.fundamental
    if f.mode == "off":
        return Gate("fund", "실적", None, "안 봄", "")
    if c.growth_ok is None:
        return Gate("fund", "실적", None, "데이터 없음", "")
    if c.growth_ok:
        return Gate("fund", "실적", True, c.growth_note)
    return Gate("fund", "실적", False, "-",
                f"{c.growth_note} (기준 EPS·매출 +{f.min_eps_growth:.0f}%)")


def _ud_gate(c, cfg: Config) -> Gate:
    f = cfg.fundamental
    if f.ud_mode == "off":
        return Gate("ud", "매집", None, "안 봄", "")
    if not np.isfinite(c.ud_ratio):
        return Gate("ud", "매집", None, "데이터 없음", "")
    if c.ud_ok:
        tag = "강함" if c.ud_ratio >= f.ud_strong else "있음"
        return Gate("ud", "매집", True, f"{c.ud_ratio:.2f} {tag}")
    return Gate("ud", "매집", False, "-", f"U/D {c.ud_ratio:.2f} (기준 {f.ud_min:.2f})")


def evaluate(c, cfg: Config) -> Row:
    """한 종목의 다섯 관문을 판정한다."""
    return Row(c, [
        _trend_gate(c, cfg),
        _vcp_gate(c, cfg),
        _timing_gate(c, cfg),
        _fund_gate(c, cfg),
        _ud_gate(c, cfg),
    ])


def collect(cands: list, cfg: Config, exclude: set | None = None) -> list:
    """관문을 max_fail 개 이하로 놓친 종목만 골라, 아쉬운 순으로 정렬한다."""
    n = cfg.near
    exclude = exclude or set()
    rows = []
    for c in cands:
        if c.ticker in exclude:
            continue
        r = evaluate(c, cfg)
        if 0 < r.n_fail <= n.max_fail:
            rows.append(r)
    # 놓친 개수가 적을수록, 같으면 RS가 높을수록 앞으로
    rows.sort(key=lambda r: (r.n_fail, -(r.cand.rs_rating if np.isfinite(r.cand.rs_rating) else 0)))
    return rows[: n.limit]
