# -*- coding: utf-8 -*-
"""평일 14:20 KST — 한국 변곡점 매수 후보 (장중 잠정).

아침 브리핑(미국)과 같은 판정(변곡점 2종 + 차트패턴 3종 → signal_rank 관문)을
한국 시가총액 상위 300종목에 장중에 돌린다. 종가 전에 살 수 있게 14:20 도착이 목표.

■ 시세는 yfinance 가 아니라 FinanceDataReader(네이버)로 받는다
  2026-09-17 09:15 KST 실측: yfinance 는 한국 종목의 당일 봉을 주지 않았고(전일까지),
  5분봉도 전일 14:55 에서 끊겨 있었다. 네이버는 당일 봉이 실시간으로 들어온다.
  (지수 KS11 은 당일 봉이 없어 전일 값으로 채워진다 — 점수의 RS 항목에만 쓰인다.)

■ 한국은 근거가 약하다 (signal_rank.py docstring)
  코스피 296종목 × 3구간 백테스트에서 세 구간 모두 플러스였던 조합은
  ATR≥6% + 거래량≥1.5x 뿐이었고 하락장 기여는 +0.24%p 로 0에 가깝다.
  그래서 2026-09-14 에 한국 발송을 뺐었고, 사용자 요청으로 2026-09-17 다시 켰다.
  대신 미국처럼 '관문 미달 종목으로 빈자리 채우기'는 하지 않고 한국 관문 통과 종목만
  싣는다. 점수(RS 40점)는 한국에서 RS 가 하락장에 해로웠으므로 순서용으로만 쓰고
  60점 컷도 적용하지 않는다. 시장 필터(SPY 50/200일선)도 한국엔 검증이 없어 쓰지 않는다.

■ 장중 판정의 한계
  14:00 무렵 당일 거래량은 하루치의 일부뿐이다. 거래량 조건(10일 평균 이상, 1.5x)이
  종가 기준보다 까다롭게 걸리므로 신호가 덜 잡히는 쪽으로 틀린다(과대 추천은 아님).

트리거: repository_dispatch "inflection-kr" — 맥 launchd 가 평일 14:17 에 찌른다.
  설치:  ./scripts/local/install-trigger.sh inflection-kr 14:17
GitHub 예약 실행은 이 레포 실측 1.5~5시간 밀려서 백업 예약을 두지 않는다.
"""
import datetime as dt
import os
import socket
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram, timeutil
import refresh_tickers
import signal_rank
from morning_digest import daejjang_tags, merge_patterns

TOP_N = 10
HISTORY_DAYS = 800          # 차트패턴(컵핸들)이 260봉까지 거슬러 본다
WORKERS = 4                 # 크게 잡으면 네이버가 동시 요청을 막는다 (kcg 와 같은 값)
BENCH = "KS11"

# 발송 창 (KST). 늦게 도착한 실행이 장 마감 후 "오늘 매수"를 보내지 않게 한다.
# 15:20 부터는 동시호가라 14:20 가격 기준 판정이 의미가 없다.
WINDOW_START = (13, 50)
WINDOW_END = (15, 20)


def _one(symbol: str, start: str) -> tuple[str, pd.DataFrame | None]:
    import FinanceDataReader as fdr

    code = symbol.split(".")[0]
    for attempt in range(2):
        try:
            df = fdr.DataReader(code, start)
            break
        except Exception:
            df = None
            time.sleep(1.5)
    if df is None or df.empty:
        return symbol, None
    df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
    df = df[df["Close"] > 0]
    return symbol, (df if len(df) >= 210 else None)


def fetch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    socket.setdefaulttimeout(8.0)
    start = (timeutil.today() - dt.timedelta(days=HISTORY_DAYS)).isoformat()
    store = {}
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for sym, df in ex.map(lambda s: _one(s, start), symbols):
            if df is not None:
                store[sym] = df
    return store


def in_window(now: dt.datetime) -> bool:
    return WINDOW_START <= (now.hour, now.minute) <= WINDOW_END


def main() -> int:
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv or os.environ.get("INFLECTION_KR_FORCE") == "1"
    now = timeutil.now()

    if not (dry or force):
        if now.weekday() > 4:
            print(f"[inflection-kr] 주말({now:%m/%d}) — 건너뜁니다.")
            return 0
        if not in_window(now):
            print(f"[inflection-kr] {now:%H:%M} KST 는 발송 창 밖 — 건너뜁니다.")
            return 0

    symbols = refresh_tickers.load_kr_symbols()
    if not symbols:
        print("[inflection-kr] 한국 종목 리스트(data/kr_top300.json) 없음", file=sys.stderr)
        return 1

    t0 = time.time()
    store = fetch(symbols)
    print(f"[inflection-kr] 일봉 {len(store)}/{len(symbols)} 수신 ({time.time() - t0:.0f}초)",
          flush=True)
    if not store:
        return 1

    # 휴장일이면 당일 봉이 없다. 가장 많은 종목의 마지막 날짜로 판단한다.
    last_day = pd.Series([df.index[-1].date() for df in store.values()]).mode()[0]
    if last_day != now.date() and not (dry or force):
        print(f"[inflection-kr] 당일 봉 없음(마지막 {last_day}) — 휴장일로 보고 건너뜁니다.")
        return 0

    import FinanceDataReader as fdr
    bench = fdr.DataReader(BENCH, (now.date() - dt.timedelta(days=HISTORY_DAYS)).isoformat())["Close"]

    patterns = merge_patterns(store)
    result = signal_rank.rank(store, bench, patterns, is_kr=True,
                              names=refresh_tickers.load_names())
    passed = result["passed"]
    print(f"[inflection-kr] 패턴 {len(patterns)}종목 · 한국 관문 통과 {len(passed)}", flush=True)

    tags = daejjang_tags(store, [m["symbol"] for m in passed[:TOP_N]])
    body = signal_rank.format_report(
        "━━━ 🎯 매수 후보 ━━━",
        {"passed": passed, "near": [], "rest": []},
        is_kr=True, limit=TOP_N, tags=tags, min_score=0,
        subtitle=(f"<i>패턴 {len(patterns)}종목 중 관문 통과 {len(passed)}개"
                  f"{f' (상위 {TOP_N}개)' if len(passed) > TOP_N else ''}</i>"),
    )

    msg = "\n".join([
        f"🇰🇷 <b>한국장 변곡점</b> · {timeutil.stamp()} {timeutil.stamp('%H:%M')} KST",
        f"<i>시총 상위 {len(store)}종목 · 장중 잠정 판정 · "
        f"ATR≥{signal_rank.ATR_MIN_PCT_KR:g}% 거래량≥{signal_rank.VOL_RATIO_MIN_KR:g}x</i>",
        "",
        body,
        "",
        f"<b>💰 종목당 계좌의 {signal_rank.POSITION_MAX_PCT}% 이하</b> · "
        f"손절 -{signal_rank.STOP_PCT}% (갭 하락 시 더 잃을 수 있음)",
        "<i>⚠️ 한국은 백테스트 엣지가 약합니다(하락장 기여 ≈ 0). "
        "당일 거래량이 아직 덜 쌓인 장중 기준이라 종가 판정과 다를 수 있습니다. 매수 추천 아님.</i>",
    ])

    if dry:
        print(msg)
        return 0
    if not telegram.send(msg):
        return 1
    print("[inflection-kr] 전송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
