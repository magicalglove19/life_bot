"""네이버 증권 시세 (인증 불필요, 국내 실시간).

야후 한국 시세는 지연되고 15:00~15:30 봉이 없어서 실전 판정에는 네이버를 쓴다.
09~14시 봉은 야후 1시간봉과 시가·고가·저가·종가·거래량이 같다 (2026-10-02 삼성전자 대조).
"""
import datetime as dt
import time

import pandas as pd
import requests

KST = dt.timezone(dt.timedelta(hours=9))
BASE = "https://api.stock.naver.com/chart/domestic/item/{code}/{tf}?startDateTime={s}&endDateTime={e}"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def _get(code: str, tf: str, start: str, end: str, retries: int = 2):
    url = BASE.format(code=code, tf=tf, s=start, e=end)
    for k in range(retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=10)
            if r.status_code == 200:
                return r.json()
        except requests.RequestException:
            pass
        time.sleep(0.5 * (k + 1))
    return None


def _frame(rows) -> pd.DataFrame:
    """분봉은 localDateTime/currentPrice, 일봉은 localDate/closePrice 로 온다."""
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    if "localDateTime" in df:
        idx = pd.to_datetime(df["localDateTime"], format="%Y%m%d%H%M%S")
        close = df["currentPrice"]
    else:
        idx = pd.to_datetime(df["localDate"], format="%Y%m%d")
        close = df["closePrice"]
    idx = idx.dt.tz_localize(KST)
    out = pd.DataFrame({
        "Open": df["openPrice"].astype(float), "High": df["highPrice"].astype(float),
        "Low": df["lowPrice"].astype(float), "Close": close.astype(float),
        "Volume": df["accumulatedTradingVolume"].astype(float),
    })
    out.index = idx
    return out.sort_index()


def hourly(code: str, now: dt.datetime) -> pd.DataFrame:
    """정규장 1시간봉 (09~15시 봉, NXT 시간외 제외). 네이버는 약 8거래일치를 준다."""
    start = (now - dt.timedelta(days=20)).strftime("%Y%m%d") + "0800"
    end = now.strftime("%Y%m%d") + "2359"
    df = _frame(_get(code, "minute60", start, end))
    if df.empty:
        return df
    return df[(df.index.hour >= 9) & (df.index.hour <= 15)]


def daily(code: str, now: dt.datetime, days: int = 90) -> pd.DataFrame:
    start = (now - dt.timedelta(days=days)).strftime("%Y%m%d") + "0000"
    end = now.strftime("%Y%m%d") + "2359"
    return _frame(_get(code, "day", start, end))
