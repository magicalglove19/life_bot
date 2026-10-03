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


def _fill_missing_last_close(data: pd.DataFrame, syms: list[str]) -> None:
    """마지막 봉의 종가가 비어 있으면 1시간봉 마지막 값으로 채운다 (data 를 직접 고친다).

    야후는 장 마감 뒤 한동안(실측 2026-10-03: 금요일 마감 후 12시간 넘게) 마지막 일봉의
    시가·고가·저가·거래량은 주면서 Close 만 NaN 으로 준다. 그대로 dropna 하면 그날이 통째로
    빠져 토요일 아침 브리핑이 목요일 데이터로 나간다. 1시간봉 마지막 봉(15:30~16:00)의 종가는
    정규장 마지막 체결가라 공식 종가(종가 단일가)와 몇 센트 다를 수 있다.
    """
    if not isinstance(data.columns, pd.MultiIndex) or len(data) == 0:
        return
    last = data.index[-1]
    need = []
    for sym in syms:
        if sym not in data.columns.get_level_values(0):
            continue
        row = data[sym].loc[last]
        if pd.isna(row["Close"]) and row[["Open", "High", "Low"]].notna().all():
            need.append(sym)
    if not need:
        return
    try:
        intra = yf.download(tickers=" ".join(need), period="5d", interval="1h", group_by="ticker",
                            threads=True, progress=False, auto_adjust=False, timeout=60)
    except Exception as e:
        print(f"[market_data] 마지막 종가 보충 실패: {e}", file=sys.stderr)
        return
    filled = 0
    for sym in need:
        try:
            c = (intra[sym]["Close"] if isinstance(intra.columns, pd.MultiIndex) else intra["Close"]).dropna()
            idx = c.index.tz_convert("America/New_York") if c.index.tz is not None else c.index
            same_day = c[idx.date == pd.Timestamp(last).date()]
            if same_day.empty:
                continue
            hi, lo = data.at[last, (sym, "High")], data.at[last, (sym, "Low")]
            data.at[last, (sym, "Close")] = min(max(float(same_day.iloc[-1]), lo), hi)
            if (sym, "Adj Close") in data.columns:
                data.at[last, (sym, "Adj Close")] = data.at[last, (sym, "Close")]
            filled += 1
        except Exception:
            continue
    print(f"[market_data] {pd.Timestamp(last).date()} 종가 비어 있음 {len(need)}종목 → 1시간봉으로 {filled}종목 보충",
          flush=True)


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
        _fill_missing_last_close(data, chunk)

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
