"""쿨라매기(Kristjan Kullamägi) 3중 이동평균선 추세 돌파 전략.

스크리너(오늘 신호)와 백테스트(과거 전체)가 **같은 함수**로 신호를 만든다.
그래서 두 결과가 어긋나지 않는다. pine/qullamaggie_triple_ma.pine 과 수식이 1:1로 대응한다.

  지표     10 EMA · 20 EMA · 50 SMA
  롱 진입  10 EMA > 20 EMA > 50 SMA
           + 직전 N봉 횡보가 후반으로 갈수록 좁아짐 + 거래량 마름
           + 종가가 직전 N봉 고점을 돌파 + 돌파일 거래량 급증
           + 직전 10봉 안에 역배열이 있었으면 '하락 직후 첫 반등'으로 보고 스킵
  손절     횡보 반대편(기본) / 돌파선 / 신호 봉 저점
  청산     종가 < 20 EMA
  재진입   포지션이 없을 때 같은 조건이 다시 나오면 진입
  숏       위를 전부 뒤집은 것

미래 정보가 섞이지 않도록 횡보 구간·거래량 평균은 전부 '신호 봉 직전'까지만 쓴다.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .config import QullConfig
from .indicators import ema, sma


# ════════════════════════════════════════════════════════════════════
#  신호 계산 (벡터)
# ════════════════════════════════════════════════════════════════════

def _cons_stats(df: pd.DataFrame, n: int, shift: int) -> dict:
    """길이 n 횡보 구간의 통계. shift=1이면 '오늘 직전까지', 0이면 '오늘 포함'."""
    H, L, C, V = df["High"], df["Low"], df["Close"], df["Volume"].astype(float)
    half = n // 2
    rest = n - half
    hi = H.rolling(n, min_periods=n).max().shift(shift)
    lo = L.rolling(n, min_periods=n).min().shift(shift)
    # 후반(최근) half봉 vs 전반 rest봉의 변동폭 — 각 구간 끝 종가 대비
    r_late = (H.rolling(half, min_periods=half).max().shift(shift)
              - L.rolling(half, min_periods=half).min().shift(shift)) / C.shift(shift)
    r_early = (H.rolling(rest, min_periods=rest).max().shift(shift + half)
               - L.rolling(rest, min_periods=rest).min().shift(shift + half)) / C.shift(shift + half)
    v_late = V.rolling(half, min_periods=half).mean().shift(shift)
    v_early = V.rolling(rest, min_periods=rest).mean().shift(shift + half)
    return {
        "hi": hi, "lo": lo,
        "range_pct": (hi - lo) / hi * 100.0,
        "r_late": r_late, "r_early": r_early,
        "v_late": v_late, "v_early": v_early,
    }


def compute_signals(df: pd.DataFrame, q: QullConfig) -> pd.DataFrame:
    """일봉(또는 임의 봉) OHLCV → 신호 컬럼이 붙은 DataFrame."""
    C, H, L, O = df["Close"], df["High"], df["Low"], df["Open"]
    V = df["Volume"].astype(float)
    out = pd.DataFrame(index=df.index)
    out["ema_fast"] = ema(C, q.ema_fast)
    out["ema_mid"] = ema(C, q.ema_mid)
    out["sma_slow"] = sma(C, q.sma_slow)
    bull = (out["ema_fast"] > out["ema_mid"]) & (out["ema_mid"] > out["sma_slow"])
    bear = (out["ema_fast"] < out["ema_mid"]) & (out["ema_mid"] < out["sma_slow"])
    out["bull"], out["bear"] = bull.fillna(False), bear.fillna(False)

    cs = _cons_stats(df, q.cons_bars, shift=1)
    out["cons_high"], out["cons_low"], out["range_pct"] = cs["hi"], cs["lo"], cs["range_pct"]
    contract = (cs["r_late"] <= cs["r_early"] * q.contraction_ratio) & (cs["range_pct"] <= q.max_range_pct)
    dry = cs["v_late"] <= cs["v_early"] * q.dryup_ratio
    out["contract_ok"], out["dryup_ok"] = contract.fillna(False), dry.fillna(False)

    vavg = V.rolling(q.vol_len, min_periods=q.vol_len).mean().shift(1)
    if q.vol_mode == "std":
        vsd = V.rolling(q.vol_len, min_periods=q.vol_len).std(ddof=0).shift(1)   # Pine ta.stdev(biased)
        spike = V > vavg + q.vol_std * vsd
    else:
        spike = V > vavg * q.vol_mult
    out["vol_ratio"] = V / vavg
    out["vol_spike"] = spike.fillna(False)

    base = out["contract_ok"] & out["dryup_ok"] & out["vol_spike"]
    long_raw = (out["bull"] & base & (C > out["cons_high"])).to_numpy()
    short_raw = (out["bear"] & base & (C < out["cons_low"])).to_numpy()
    out["long_raw"], out["short_raw"] = long_raw, short_raw

    # 첫 반등 스킵 — 직전 W봉(오늘 제외) 안에 반대 배열이 있었으면 버린다. Pine: ta.highest(x?1:0, W)[1] > 0
    W = max(1, int(q.first_bounce_bars))
    bear_recent = (out["bear"].astype(float).rolling(W, min_periods=W).max().shift(1) > 0).to_numpy()
    bull_recent = (out["bull"].astype(float).rolling(W, min_periods=W).max().shift(1) > 0).to_numpy()
    prev_l = np.r_[False, long_raw[:-1]]
    prev_s = np.r_[False, short_raw[:-1]]
    long_event = long_raw & ~prev_l          # 연속된 날의 중복 신호는 첫날만
    short_event = short_raw & ~prev_s
    skip_l = bear_recent if q.skip_first_signal else np.zeros(len(df), bool)
    skip_s = bull_recent if q.skip_first_signal else np.zeros(len(df), bool)
    out["long_signal"] = long_event & ~skip_l
    out["short_signal"] = short_event & ~skip_s
    out["long_skipped_first"] = long_event & skip_l
    out["short_skipped_first"] = short_event & skip_s
    # 대기 종목이 내일 돌파하면 스킵될 자리인지 (오늘 포함 W봉)
    out["bear_within"] = (out["bear"].astype(float).rolling(W, min_periods=W).max() > 0).to_numpy()

    # 손절선
    adr = ((H / L - 1.0) * 100.0).rolling(20, min_periods=20).mean()
    out["adr"] = adr
    if q.stop_mode == "breakout":
        ls, ss = out["cons_high"], out["cons_low"]
    elif q.stop_mode == "signal_bar":
        ls, ss = L, H
    else:
        ls, ss = out["cons_low"], out["cons_high"]
    if q.adr_stop_cap > 0:
        cap = adr * q.adr_stop_cap / 100.0
        ls = np.maximum(ls, C * (1 - cap))
        ss = np.minimum(ss, C * (1 + cap))
    out["long_stop"], out["short_stop"] = ls, ss
    out["open"], out["high"], out["low"], out["close"] = O, H, L, C
    return out


# ════════════════════════════════════════════════════════════════════
#  체결 시뮬레이션 (한 종목)
# ════════════════════════════════════════════════════════════════════

@dataclass
class Trade:
    side: str                 # "long" | "short"
    entry_date: pd.Timestamp
    entry: float
    stop: float
    exit_date: pd.Timestamp | None = None
    exit: float = np.nan
    reason: str = ""          # "손절" | "20EMA 이탈" | "기간 종료"
    bars: int = 0

    @property
    def ret_pct(self) -> float:
        if not np.isfinite(self.exit):
            return np.nan
        r = self.exit / self.entry - 1.0 if self.side == "long" else self.entry / self.exit - 1.0
        return r * 100.0

    @property
    def risk_pct(self) -> float:
        return abs(self.entry - self.stop) / self.entry * 100.0

    @property
    def r_multiple(self) -> float:
        rp = self.risk_pct
        return self.ret_pct / rp if rp > 0 else np.nan


def simulate(sig: pd.DataFrame, side: str = "long", fill: str = "close",
             fee_pct: float = 0.0, start=None) -> tuple[list[Trade], Trade | None]:
    """신호 DataFrame 위에서 규칙대로 사고판다. (완료 거래, 아직 열린 거래)

    fill="close": 신호 봉 종가 진입 (전략 원문, Pine process_orders_on_close)
    fill="open" : 다음 봉 시가 진입 (더 보수적)
    손절은 진입 다음 봉부터 장중 체크(갭이면 시가 체결), 20 EMA 이탈은 종가 체결.
    같은 봉에 청산했으면 재진입은 다음 봉부터 — Pine 브로커 에뮬레이터와 같은 순서.
    """
    idx = sig.index
    O, H, L, C = (sig[k].to_numpy(float) for k in ("open", "high", "low", "close"))
    mid = sig["ema_mid"].to_numpy(float)
    sides = ("long", "short") if side == "both" else (side,)
    lsig, ssig = sig["long_signal"].to_numpy(bool), sig["short_signal"].to_numpy(bool)
    lstop, sstop = sig["long_stop"].to_numpy(float), sig["short_stop"].to_numpy(float)
    first = 0 if start is None else int(idx.searchsorted(pd.Timestamp(start)))
    fee = fee_pct / 100.0

    trades: list[Trade] = []
    pos: Trade | None = None
    pending: tuple[str, float] | None = None   # fill="open"일 때 다음 봉에 진입할 주문
    entry_i = -1

    def close_trade(i, price, reason):
        nonlocal pos
        adj = price * (1 - fee) if pos.side == "long" else price * (1 + fee)
        pos.exit_date, pos.exit, pos.reason, pos.bars = idx[i], adj, reason, i - entry_i
        trades.append(pos)
        pos = None

    for i in range(max(first, 1), len(sig)):
        if pending is not None and pos is None:
            s_, stop_ = pending
            px = O[i]
            valid = (s_ == "long" and px > stop_) or (s_ == "short" and px < stop_)
            if valid:
                pos = Trade(s_, idx[i], px * (1 + fee) if s_ == "long" else px * (1 - fee), stop_)
                entry_i = i
            pending = None

        if pos is not None and i > entry_i:
            if pos.side == "long":
                if L[i] <= pos.stop:
                    close_trade(i, min(O[i], pos.stop), "손절")
                elif np.isfinite(mid[i]) and C[i] < mid[i]:
                    close_trade(i, C[i], "20EMA 이탈")
            else:
                if H[i] >= pos.stop:
                    close_trade(i, max(O[i], pos.stop), "손절")
                elif np.isfinite(mid[i]) and C[i] > mid[i]:
                    close_trade(i, C[i], "20EMA 이탈")
            if pos is None:
                continue            # 청산한 봉에서는 재진입하지 않는다

        if pos is None and pending is None:
            for s_ in sides:
                fire = lsig[i] if s_ == "long" else ssig[i]
                stop_ = lstop[i] if s_ == "long" else sstop[i]
                if not fire or not np.isfinite(stop_):
                    continue
                if s_ == "long" and not stop_ < C[i]:
                    continue
                if s_ == "short" and not stop_ > C[i]:
                    continue
                if fill == "open":
                    pending = (s_, stop_)
                else:
                    pos = Trade(s_, idx[i], C[i] * (1 + fee) if s_ == "long" else C[i] * (1 - fee), stop_)
                    entry_i = i
                break

    return trades, pos


# ════════════════════════════════════════════════════════════════════
#  오늘 기준 스크리닝
# ════════════════════════════════════════════════════════════════════

@dataclass
class QullPick:
    ticker: str
    name: str = ""
    sector: str = ""
    kind: str = ""            # "long_today" | "short_today" | "watch" | "open_long" | "skipped_first"
    price: float = np.nan
    trigger: float = np.nan   # 진입가(오늘 신호) 또는 돌파 트리거(대기)
    stop: float = np.nan
    exit_line: float = np.nan  # 20 EMA
    vol_ratio: float = np.nan
    range_pct: float = np.nan
    dist_pct: float = np.nan   # 대기: 트리거까지 남은 %
    entry_date: pd.Timestamp | None = None
    open_ret_pct: float = np.nan
    first_in_regime: bool = False  # 대기 종목이 내일 돌파해도 '하락 직후 반등'이라 스킵될 자리인가
    shares: int = 0
    also_minervini: str = ""   # 미너비니 쪽 상태(교집합 표시)
    extra: dict = field(default_factory=dict)

    @property
    def stop_pct(self) -> float:
        ref = self.trigger if np.isfinite(self.trigger) else self.price
        return abs(ref - self.stop) / ref * 100.0 if ref and np.isfinite(self.stop) else np.nan


def _size(entry: float, stop: float, account: float, risk_pct: float, max_pos_pct: float) -> int:
    risk = abs(entry - stop)
    if not (np.isfinite(risk) and risk > 0 and entry > 0):
        return 0
    return max(0, min(math.floor(account * risk_pct / 100 / risk), math.floor(account * max_pos_pct / 100 / entry)))


def scan_today(frames: dict, meta: dict, cfg, open_lookback: int = 60) -> dict[str, list[QullPick]]:
    """frames = {티커: OHLCV}. 마지막 봉 기준으로 신호/대기/진행 중 거래를 뽑는다."""
    q = cfg.qull
    res = {"long_today": [], "short_today": [], "watch": [], "open_long": [], "skipped_first": []}
    min_len = q.sma_slow + q.cons_bars + 5
    for tk, df in frames.items():
        if tk == cfg.benchmark or df is None or len(df) < min_len:
            continue
        df = df.dropna(subset=["Open", "High", "Low", "Close"])
        if len(df) < min_len or float(df["Close"].iloc[-1]) < cfg.min_price:
            continue
        sig = compute_signals(df, q)
        last = sig.iloc[-1]
        name, sector = meta.get(tk, (tk, ""))
        price = float(last["close"])
        base = dict(ticker=tk, name=name, sector=sector, price=price, exit_line=float(last["ema_mid"]),
                    vol_ratio=float(last["vol_ratio"]), range_pct=float(last["range_pct"]))

        if bool(last["long_signal"]):
            st = float(last["long_stop"])
            res["long_today"].append(QullPick(kind="long_today", trigger=price, stop=st,
                                              shares=_size(price, st, cfg.risk.account_size,
                                                           cfg.risk.risk_per_trade_pct, cfg.risk.max_position_pct),
                                              **base))
        elif bool(last["long_skipped_first"]):
            res["skipped_first"].append(QullPick(kind="skipped_first", trigger=price,
                                                 stop=float(last["long_stop"]), **base))
        if bool(last["short_signal"]):
            res["short_today"].append(QullPick(kind="short_today", trigger=price,
                                               stop=float(last["short_stop"]), **base))

        # 진행 중 롱: 최근 구간을 규칙대로 돌려 아직 청산 안 된 거래
        start = sig.index[max(0, len(sig) - open_lookback)]
        _, pos = simulate(sig, "long", "close", 0.0, start=start)
        if pos is not None and pos.entry_date != sig.index[-1]:
            res["open_long"].append(QullPick(kind="open_long", trigger=pos.entry, stop=pos.stop,
                                             entry_date=pos.entry_date,
                                             open_ret_pct=(price / pos.entry - 1) * 100, **base))

        # 돌파 대기: 오늘 포함 N봉이 수축·마름 상태이고 트리거(구간 고점)에 가깝다
        if bool(last["bull"]) and not bool(last["long_signal"]):
            cs = _cons_stats(df, q.cons_bars, shift=0)
            hi, lo = float(cs["hi"].iloc[-1]), float(cs["lo"].iloc[-1])
            ok = (float(cs["r_late"].iloc[-1]) <= float(cs["r_early"].iloc[-1]) * q.contraction_ratio
                  and float(cs["range_pct"].iloc[-1]) <= q.max_range_pct
                  and float(cs["v_late"].iloc[-1]) <= float(cs["v_early"].iloc[-1]) * q.dryup_ratio)
            dist = (hi / price - 1.0) * 100.0 if price > 0 else np.nan
            if ok and np.isfinite(dist) and 0 <= dist <= q.watch_within_pct:
                trig = hi
                if q.stop_mode == "breakout":
                    st = hi
                elif q.stop_mode == "signal_bar":
                    st = float(df["Low"].iloc[-1])
                else:
                    st = lo
                res["watch"].append(QullPick(kind="watch", trigger=trig, stop=st, dist_pct=dist,
                                             range_pct=float(cs["range_pct"].iloc[-1]),
                                             first_in_regime=bool(q.skip_first_signal and last["bear_within"]),
                                             shares=_size(trig, st, cfg.risk.account_size,
                                                          cfg.risk.risk_per_trade_pct, cfg.risk.max_position_pct),
                                             **{k: v for k, v in base.items() if k != "range_pct"}))

    res["long_today"].sort(key=lambda p: -p.vol_ratio)
    res["short_today"].sort(key=lambda p: -p.vol_ratio)
    res["watch"].sort(key=lambda p: p.dist_pct)
    res["open_long"].sort(key=lambda p: -p.open_ret_pct)
    return res
