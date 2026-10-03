"""실시간 판정 — 백테스트(backtest.py)와 같은 규칙을 '지금 막 끝난 1시간봉' 하나에 적용한다.

규칙 (README '알림 봇으로 옮길 규칙'):
  박스   직전 7거래일(42개) 1시간봉. 폭 ≤ 일봉 ATR×3, 종가가 POC 를 4번 이상 건넘
  신호   종가 > 박스 상단, 전일 기준 20일선 위, 거래량 ≥ 직전 42개 봉 평균×1.5
  손절   POC. 진입가-POC 가 일봉 ATR×0.25 미만이면 신호 없음
  익절   +1R 50% (잔량 손절 본전), +3R 잔량. 5거래일 지나면 정리

판정에 쓰는 봉은 09~14시 봉만이다 (백테스트 데이터인 야후 1시간봉에 15시 봉이 없다).
가상 매매 관리는 15시 봉(15:00~15:30)까지 쓴다 — 실제로 손절이 걸리는 시간이므로.
"""
import datetime as dt

import numpy as np
import pandas as pd

from .levels import profile

RULE = dict(box_days=7, bpd=6, max_width_atr=3.0, min_cross=4, min_rv_box=1.5,
            min_risk_atr=0.25, t1_r=1.0, t2_r=3.0, hold_days=5, fake_window=3)


def merge_bars(yahoo: pd.DataFrame, naver: pd.DataFrame, today: dt.date) -> pd.DataFrame:
    """어제까지는 야후, 오늘(그리고 네이버가 가진 최근 며칠)은 네이버.

    야후 한국 1시간봉은 당일 첫 봉 거래량이 0 으로 오는 등 장중 값이 불완전하다.
    겹치는 시각은 네이버 값을 쓴다 (09~14시 봉은 두 출처가 같다).
    """
    y = yahoo[yahoo.index.date < today] if not yahoo.empty else yahoo
    if naver.empty:
        return y.sort_index()
    y = y[~y.index.isin(naver.index)]
    return pd.concat([y, naver]).sort_index()


def regular(bars: pd.DataFrame) -> pd.DataFrame:
    """판정용 09~14시 봉."""
    return bars[(bars.index.hour >= 9) & (bars.index.hour <= 14)]


def completed(bars: pd.DataFrame, now: dt.datetime) -> pd.DataFrame:
    """지금 시각 기준으로 끝난 봉만. 15시 봉은 15:30 에 끝난다."""
    end = bars.index + pd.Timedelta(hours=1)
    end = end.where(bars.index.hour != 15, bars.index + pd.Timedelta(minutes=30))
    return bars[end <= now]


def daily_context(reg: pd.DataFrame, day: dt.date):
    """day 의 전일까지 기준 일봉 ATR(20)·20일선. 백테스트처럼 1시간봉을 일봉으로 묶어 계산."""
    d = reg.groupby(reg.index.date).agg(High=("High", "max"), Low=("Low", "min"),
                                         Close=("Close", "last"))
    d = d[d.index < day]
    if len(d) < 20:
        return np.nan, np.nan
    pc = d["Close"].shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - pc).abs(), (d["Low"] - pc).abs()], axis=1).max(axis=1)
    return tr.iloc[-20:].mean(), d["Close"].iloc[-20:].mean()


def evaluate(reg: pd.DataFrame, i: int, rule=RULE):
    """reg 의 i 번째 봉(마감된 봉)이 돌파 매수 신호인지. 판정에 쓴 값을 전부 돌려준다."""
    n = rule["box_days"] * rule["bpd"]
    if i < n:
        return None
    w = reg.iloc[i - n:i]
    bar = reg.iloc[i]
    atr, sma = daily_context(reg.iloc[:i + 1], reg.index[i].date())
    if np.isnan(atr):
        return None
    poc, _, _ = profile(w["High"].values, w["Low"].values, w["Volume"].values)
    side = np.sign(w["Close"].values - poc)
    side = side[side != 0]
    cross = int((np.diff(side) != 0).sum()) if len(side) > 1 else 0
    box_hi, box_lo = w["High"].max(), w["Low"].min()
    rng = bar["High"] - bar["Low"]
    r = dict(
        time=reg.index[i], close=bar["Close"], box_hi=box_hi, box_lo=box_lo, poc=poc,
        cross=cross, atr=atr, sma20=sma, width_atr=(box_hi - box_lo) / atr,
        rv_box=bar["Volume"] / w["Volume"].mean(),
        body=(bar["Close"] - bar["Open"]) / rng if rng > 0 else 0.0,
        first_bar=i == 0 or reg.index[i - 1].date() != reg.index[i].date(),
    )
    r["balanced"] = r["width_atr"] <= rule["max_width_atr"] and cross >= rule["min_cross"]
    r["breakout"] = bar["Close"] > box_hi
    r["trend"] = bar["Close"] > sma
    r["volume"] = r["rv_box"] >= rule["min_rv_box"]
    # 진입가는 다음 봉 시가라 아직 모른다. 지금 종가로 손절폭을 가늠한다.
    r["room"] = bar["Close"] - poc >= rule["min_risk_atr"] * atr
    r["signal"] = all(r[k] for k in ("balanced", "breakout", "trend", "volume", "room"))
    return r


# ── 가상 매매 ─────────────────────────────────────────────

def new_trade(code: str, name: str, src: str, sig: dict) -> dict:
    return dict(code=code, name=name, src=src, signal_time=sig["time"].isoformat(),
                level=float(sig["box_hi"]), poc=float(sig["poc"]), rv_box=float(sig["rv_box"]),
                signal_close=float(sig["close"]),
                entry=None, entry_time=None, stop=None, t1=None, t2=None, risk=None,
                half=False, status="open", r=None, exit_reason=None, exit_time=None,
                last_bar=sig["time"].isoformat(), days=[], fake_checked=0, fake_warned=False)


def step(tr: dict, bars: pd.DataFrame, rule=RULE) -> list[tuple[str, dict]]:
    """마지막으로 본 봉 이후의 마감된 봉들로 가상 매매를 굴린다. 일어난 일을 돌려준다.

    bars: 09~15시 마감된 봉 (completed(merge) 결과)
    """
    ev = []
    last = pd.Timestamp(tr["last_bar"])
    for t, b in bars[bars.index > last].iterrows():
        tr["last_bar"] = t.isoformat()
        if tr["status"] != "open":
            break
        # 가짜 돌파 경고: 신호 뒤 판정용 봉 3개 안에 박스 안으로 마감
        if t.hour <= 14 and tr["fake_checked"] < rule["fake_window"]:
            tr["fake_checked"] += 1
            if b["Close"] < tr["level"] and not tr["fake_warned"]:
                tr["fake_warned"] = True
                ev.append(("fake", dict(time=t, close=b["Close"])))
        if tr["entry"] is None:
            tr["entry"], tr["entry_time"] = float(b["Open"]), t.isoformat()
            risk = tr["entry"] - tr["poc"]
            if risk <= 0:
                tr.update(status="cancelled", exit_reason="시가가 POC 아래 — 진입 취소", exit_time=t.isoformat())
                ev.append(("cancel", dict(time=t)))
                break
            tr.update(stop=tr["poc"], risk=risk, t1=tr["entry"] + rule["t1_r"] * risk,
                      t2=tr["entry"] + rule["t2_r"] * risk)
        elif b["Open"] <= tr["stop"]:
            _exit(tr, b["Open"], t, "갭손절" if not tr["half"] else "본전(갭)")
            ev.append(("exit", dict(time=t)))
            break
        day = t.date().isoformat()
        if day not in tr["days"]:
            tr["days"].append(day)
        if b["Low"] <= tr["stop"]:
            _exit(tr, tr["stop"], t, "본전" if tr["half"] else "손절")
            ev.append(("exit", dict(time=t)))
            break
        if not tr["half"] and b["High"] >= tr["t1"]:
            tr["half"], tr["stop"] = True, tr["entry"]
            ev.append(("t1", dict(time=t)))
        if tr["half"] and b["High"] >= tr["t2"]:
            _exit(tr, tr["t2"], t, "최종목표")
            ev.append(("exit", dict(time=t)))
            break
        # 시간 청산: 진입일 포함 5거래일째 14시 봉 마감 (15:01 알림에서 바로 정리할 수 있게)
        if len(tr["days"]) >= rule["hold_days"] and t.hour == 14:
            _exit(tr, b["Close"], t, "시간청산")
            ev.append(("exit", dict(time=t)))
            break
    return ev


def _exit(tr, price, t, reason):
    r_rest = (price - tr["entry"]) / tr["risk"]
    tr["r"] = 0.5 * 1.0 + 0.5 * r_rest if tr["half"] else r_rest
    tr.update(status="closed", exit_reason=reason, exit_time=t.isoformat(), exit_price=float(price))
