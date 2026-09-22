# -*- coding: utf-8 -*-
"""강남자리(데이짱 강남 기법) 알림 — 한국 14:30, 미국 06:00 (KST).

강남불패 5대 조건 중 데이터로 확인 가능한 4가지를 본다.
  ① 양지 차트  : 20일선 > 60일선 > 120일선 정배열, 60·120일선 우상향
  ② 발 아래 수렴: 5·10·20·60·120일선이 현재가 바로 아래에 촘촘히 모여 있음
  ③ 상방 공간  : 현재가 위에서 거래된 매물이 거의 없음 (홀쭉이 자리)
  ④ 메이저 수급 : 최근 3거래일 기관·외국인이 모두 순매수 (한국만, 네이버 증권)
  ⑤ 실적 턴어라운드는 확인하지 않는다 — 메시지에서 직접 확인하라고 안내한다.

기준값은 한국 241종목 2023-09~2026-09 백테스트에서 두 해 모두 고르게 좋았던 조합이다.
20거래일 보유 승률 61%(손절 -10% 적용), 같은 기간 아무 종목이나 산 경우 45%.
미국은 같은 규칙이 기준선보다 나빴다(수급·실적을 볼 수 없다). 그래서 미국 메시지에는
경고를 붙인다.

⚠️ 규칙이나 기준값을 바꾸면 데스크톱 앱의 daejjang-trading/gangnam.js 도 같이 바꿔야 한다.

사용:
  python scripts/gangnam.py --market kr [--dry-run] [--force]
  python scripts/gangnam.py --market us [--dry-run] [--force]
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

import market_data
from common import telegram
from common.timeutil import KST

MA_PERIODS = [5, 10, 20, 60, 120]
CONVERGENCE_RANGE = 0.06    # 이평선이 현재가 아래 6% 안에 모여 있을 것
HEADROOM = 0.02             # 눌림으로 현재가보다 2% 위에 있는 이평선까지는 허용
TREND_LOOKBACK = 20         # 60·120일선이 20거래일 전보다 올라와 있을 것
OVERHEAD_LOOKBACK = 250     # 약 1년치 매물대
OVERHEAD_MAX = 0.15         # 현재가 위에서 거래된 물량이 15% 이하
SUPPLY_DAYS = 3             # 기관·외국인 순매수 확인 기간
STOP_LOSS = 0.10            # 손절 -10% (백테스트에서 5~8%보다 좋았다)

MIN_BARS = max(MA_PERIODS) + TREND_LOOKBACK + 1

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / "data" / "gangnam_state.json"

# 장중 잠정 판정이라 발송 창을 벗어난 실행(예약 지연)은 건너뛴다
MARKETS = {
    "kr": {
        "label": "한국",
        "tickers": "kr_top300.json",
        "window": ((13, 50), (15, 20)),
        "weekdays": {0, 1, 2, 3, 4},      # 월~금
        "supply": True,
    },
    "us": {
        "label": "미국",
        "tickers": "us_top400.json",
        "window": ((5, 0), (9, 0)),
        "weekdays": {1, 2, 3, 4, 5},      # 화~토 (미국 전일 장 마감 후)
        "supply": False,
    },
}

NAVER_TREND = "https://m.stock.naver.com/api/stock/{code}/trend"
HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}


def sma(values: np.ndarray, period: int) -> np.ndarray:
    c = np.cumsum(values, dtype=float)
    c[period:] = c[period:] - c[:-period]
    return c[period - 1:] / period


def load_universe(filename: str) -> list[dict]:
    data = json.loads((ROOT / "data" / filename).read_text(encoding="utf-8"))
    return data["tickers"]


def analyze(symbol: str, df) -> dict | None:
    """가격 조건 ①②③ 판정. 조건을 못 갖추면 None."""
    if df is None or len(df) < MIN_BARS:
        return None
    closes = df["Close"].to_numpy(dtype=float)
    volumes = np.nan_to_num(df["Volume"].to_numpy(dtype=float))
    if np.isnan(closes[-1]):
        return None

    mas = {}
    for period in MA_PERIODS:
        series = sma(closes, period)
        if len(series) <= TREND_LOOKBACK:
            return None
        mas[period] = series

    price = closes[-1]
    values = [mas[p][-1] for p in MA_PERIODS]

    # ① 양지 차트
    yangji = (mas[20][-1] > mas[60][-1] > mas[120][-1]
              and mas[60][-1] > mas[60][-1 - TREND_LOOKBACK]
              and mas[120][-1] > mas[120][-1 - TREND_LOOKBACK])

    # ② 발 아래 수렴
    ma_high, ma_low = max(values), min(values)
    converged = ma_high <= price * (1 + HEADROOM) and ma_low >= price * (1 - CONVERGENCE_RANGE)

    # ③ 상방 공간 — 현재가보다 위에서 거래된 물량 비율
    window_close = closes[-(OVERHEAD_LOOKBACK + 1):-1]
    window_volume = volumes[-(OVERHEAD_LOOKBACK + 1):-1]
    total = window_volume.sum()
    overhead = float(window_volume[window_close > price].sum() / total) if total > 0 else 1.0

    if not (yangji and converged and overhead <= OVERHEAD_MAX):
        return None

    return {
        "symbol": symbol,
        "price": float(price),
        "stop_price": float(price * (1 - STOP_LOSS)),
        "ma_spread": float((ma_high - ma_low) / price),
        "overhead": overhead,
        "date": df.index[-1].date().isoformat(),
    }


def naver_supply(symbol: str) -> dict | None:
    """최근 SUPPLY_DAYS 거래일 기관·외국인 순매수 합계 (주). 실패하면 None."""
    code = symbol.split(".")[0]
    try:
        res = requests.get(NAVER_TREND.format(code=code), params={"pageSize": SUPPLY_DAYS},
                           headers=HEADERS, timeout=10)
        rows = res.json()
    except Exception as e:
        print(f"[gangnam] {symbol} 수급 조회 실패: {e}", file=sys.stderr)
        return None
    if not isinstance(rows, list) or not rows:
        return None

    def num(text: str) -> float:
        return float(str(text or "0").replace(",", "").replace("+", "") or 0)

    organ = sum(num(r.get("organPureBuyQuant")) for r in rows)
    foreign = sum(num(r.get("foreignerPureBuyQuant")) for r in rows)
    return {"organ": organ, "foreign": foreign, "days": len(rows),
            "buying": organ > 0 and foreign > 0}


def shares(value: float) -> str:
    sign = "+" if value > 0 else "-" if value < 0 else ""
    abs_value = abs(value)
    if abs_value >= 10000:
        return f"{sign}{abs_value / 10000:,.1f}만주"
    return f"{sign}{abs_value:,.0f}주"


def money(value: float, market: str) -> str:
    return f"{value:,.0f}원" if market == "kr" else f"${value:,.2f}"


def build_message(market: str, picks: list[dict], names: dict[str, str], now: dt.datetime) -> str:
    label = MARKETS[market]["label"]
    lines = [f"🏆 <b>강남자리 {label}</b> · {now:%m/%d %H:%M}",
             f"양지 + 발 아래 수렴 + 상방 공간{' + 기관·외국인 양매수' if market == 'kr' else ''}",
             ""]

    for i, p in enumerate(picks, 1):
        name = names.get(p["symbol"], p["symbol"])
        if len(name) > 24:                     # 미국 종목명이 길면 폰에서 줄이 넘친다
            name = name[:23] + "…"
        code = p["symbol"].split(".")[0]
        lines.append(f"{i}. <b>{name}</b> ({code})")
        lines.append(f"   현재가 {money(p['price'], market)} · 손절가 {money(p['stop_price'], market)} (-10%)")
        lines.append(f"   위쪽 매물 {p['overhead'] * 100:.1f}% · 이평선 폭 {p['ma_spread'] * 100:.1f}%")
        if p.get("supply"):
            s = p["supply"]
            lines.append(f"   기관 {shares(s['organ'])} · 외국인 {shares(s['foreign'])} (3일)")
        lines.append("")

    if market == "kr":
        lines.append("※ 장중 잠정치입니다. 종가와 수급이 바뀔 수 있습니다.")
    else:
        lines.append("※ 미국은 수급·실적을 확인할 수 없고, 백테스트에서 효과가 확인되지 않았습니다. 참고용입니다.")
    lines.append("※ 실적 턴어라운드(⑤)는 직접 확인하세요.")
    return "\n".join(lines)


def in_window(market: str, now: dt.datetime) -> bool:
    start, end = MARKETS[market]["window"]
    return start <= (now.hour, now.minute) <= end


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def mark_sent(market: str, day: str) -> None:
    state = load_state()
    state[market] = {"last_sent": day}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main() -> int:
    argv = sys.argv[1:]
    market = "kr"
    if "--market" in argv:
        market = argv[argv.index("--market") + 1].lower()
    if market not in MARKETS:
        print(f"[gangnam] 알 수 없는 시장: {market}", file=sys.stderr)
        return 2

    dry = "--dry-run" in argv
    force = "--force" in argv or os.environ.get("GANGNAM_FORCE") == "1"
    now = dt.datetime.now(KST)
    today = now.date().isoformat()
    conf = MARKETS[market]

    if not (dry or force):
        if now.weekday() not in conf["weekdays"]:
            print(f"[gangnam] {conf['label']} 발송 요일이 아닙니다 ({now:%m/%d %a}) — 건너뜁니다.")
            return 0
        if not in_window(market, now):
            print(f"[gangnam] 발송 창({conf['window'][0][0]:02d}:{conf['window'][0][1]:02d}"
                  f"~{conf['window'][1][0]:02d}:{conf['window'][1][1]:02d}) 밖 {now:%H:%M} — 건너뜁니다.")
            return 0
        if load_state().get(market, {}).get("last_sent") == today:
            print(f"[gangnam] {conf['label']} 오늘 이미 발송했습니다 — 건너뜁니다.")
            return 0

    universe = load_universe(conf["tickers"])
    names = {t["symbol"]: t.get("name") or t["symbol"] for t in universe}
    symbols = [t["symbol"] for t in universe]

    print(f"[gangnam] {conf['label']} {len(symbols)}종목 시세 수집...")
    store = market_data.fetch(symbols, period="2y")
    print(f"[gangnam] 시세 확보 {len(store)}종목")

    picks = [r for r in (analyze(sym, store.get(sym)) for sym in symbols) if r]
    print(f"[gangnam] 가격 조건 통과 {len(picks)}종목")

    # 데이터가 오래됐으면(휴장 등) 어제 자리로 알림이 가지 않게 막는다
    if picks and not (dry or force):
        latest = max(p["date"] for p in picks)
        stale_days = (now.date() - dt.date.fromisoformat(latest)).days
        limit = 0 if market == "kr" else 3
        if stale_days > limit:
            print(f"[gangnam] 최신 시세가 {latest} 뿐입니다 (휴장 추정) — 건너뜁니다.")
            return 0

    # ④ 수급은 가격 조건을 통과한 종목만 조회한다 (네이버 호출 최소화)
    if conf["supply"] and picks:
        with ThreadPoolExecutor(max_workers=4) as pool:
            supplies = list(pool.map(lambda p: naver_supply(p["symbol"]), picks))
        for p, s in zip(picks, supplies):
            p["supply"] = s
        waiting = [p for p in picks if not (p.get("supply") and p["supply"]["buying"])]
        picks = [p for p in picks if p.get("supply") and p["supply"]["buying"]]
        print(f"[gangnam] 수급까지 통과 {len(picks)}종목 (수급 대기 {len(waiting)}종목)")

    if not picks:
        print(f"[gangnam] {conf['label']} 강남자리 없음 — 발송하지 않습니다.")
        return 0

    picks.sort(key=lambda p: p["overhead"])  # 가벼운(매물 적은) 순
    text = build_message(market, picks, names, now)

    if dry:
        print(text)
        return 0

    if telegram.send(text):
        mark_sent(market, today)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
