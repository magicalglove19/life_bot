# -*- coding: utf-8 -*-
"""종목 시세 일괄 다운로드 (공용).

예전에는 변곡점·강남자리 스캐너가 각자 yfinance를 종목당 1회씩 호출했다.
400종목이면 400번이라 타임아웃에 걸려 리스트 앞부분만 스캔되고 끊겼고,
그 결과 스캐너마다 커버한 종목이 달라서 '여러 스캐너에 동시에 걸린 종목'을
셀 수가 없었다. 여기서 한 번에 받아 모든 스캐너가 같은 데이터를 공유한다.
"""
import sys
import time

import pandas as pd
import yfinance as yf

CHUNK = 100
MIN_BARS = 210          # 강남자리가 MA200을 쓰므로 최소 210봉
DEFAULT_PERIOD = "2y"   # MA200 + 컵위드핸들 최대 260봉 lookback 확보


def fetch(symbols: list[str], period: str = DEFAULT_PERIOD,
          retries: int = 2) -> dict[str, pd.DataFrame]:
    """{심볼: OHLCV DataFrame}. 실패한 종목은 조용히 빠진다."""
    store: dict[str, pd.DataFrame] = {}
    if not symbols:
        return store

    for i in range(0, len(symbols), CHUNK):
        chunk = symbols[i:i + CHUNK]
        data = None
        for attempt in range(retries + 1):
            try:
                data = yf.download(
                    tickers=" ".join(chunk), period=period, interval="1d",
                    group_by="ticker", threads=True, progress=False,
                    auto_adjust=False, timeout=60,
                )
                break
            except Exception as e:
                if attempt == retries:
                    print(f"[market_data] 청크 {i} 실패: {e}", file=sys.stderr)
                else:
                    time.sleep(3)

        if data is None or len(data) == 0:
            continue

        for sym in chunk:
            try:
                if isinstance(data.columns, pd.MultiIndex):
                    if sym not in data.columns.get_level_values(0):
                        continue
                    df = data[sym]
                else:
                    df = data
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
                if len(df) < MIN_BARS:
                    continue
                if df.index.tz is not None:
                    df.index = df.index.tz_localize(None)
                store[sym] = df
            except Exception:
                continue

        print(f"[market_data] {min(i + CHUNK, len(symbols))}/{len(symbols)} "
              f"수신 {len(store)}", flush=True)

    return store
