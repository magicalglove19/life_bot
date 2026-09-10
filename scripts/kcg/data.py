"""FinanceDataReader 로 국내 일봉을 내려받고 캐싱한다."""

from __future__ import annotations

import datetime as dt
import os
import pickle
import socket
import time
import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from .indicators import add_indicators

warnings.filterwarnings("ignore")

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_REQUIRED = ["Open", "High", "Low", "Close", "Volume"]

# 상장폐지·거래정지 종목에서 응답이 오지 않고 매달리는 경우가 있다.
# FinanceDataReader 는 timeout 인자를 받지 않으므로 소켓 기본값으로 막는다.
TIMEOUT = 8.0
RETRIES = 2          # 스로틀링에 걸린 요청은 잠깐 쉬었다 한 번 더
BACKOFF = 1.5


def _cache_file(tag: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"prices_{tag}_{dt.date.today().isoformat()}.pkl")


def _purge_old(tag: str, keep_days: int = 5) -> None:
    cutoff = dt.date.today() - dt.timedelta(days=keep_days)
    if not os.path.isdir(CACHE_DIR):
        return
    prefix = f"prices_{tag}_"
    for fn in os.listdir(CACHE_DIR):
        if not (fn.startswith(prefix) and fn.endswith(".pkl")):
            continue
        try:
            stamp = dt.date.fromisoformat(fn[len(prefix):-4])
        except ValueError:
            continue
        if stamp < cutoff:
            os.remove(os.path.join(CACHE_DIR, fn))


def _one(code: str, start: str) -> tuple[str, pd.DataFrame | None]:
    """한 종목의 일봉. 네이버가 동시 요청을 조일 때가 있어 짧게 재시도한다."""
    import FinanceDataReader as fdr

    df = None
    for attempt in range(RETRIES):
        try:
            df = fdr.DataReader(code, start)
            break
        except Exception:
            if attempt + 1 < RETRIES:
                time.sleep(BACKOFF)
    if df is None or df.empty or not set(_REQUIRED).issubset(df.columns):
        return code, None
    df = df[_REQUIRED].dropna(how="all")
    df = df[df["Close"] > 0]
    return code, (df if len(df) >= 30 else None)


def download(
    codes: list[str],
    history_days: int = 500,
    workers: int = 4,
    use_cache: bool = True,
    tag: str = "krx",
    progress=None,
) -> dict[str, pd.DataFrame]:
    """{종목코드: 지표가 붙은 일봉 DataFrame} 을 반환한다.

    같은 날 두 번째 실행부터는 캐시를 쓴다(--no-cache 로 무시).
    workers 를 크게 잡으면 네이버가 동시 요청을 막아 오히려 느려진다 (4~6 권장).
    """
    path = _cache_file(tag)
    cached: dict[str, pd.DataFrame] = {}
    if use_cache and os.path.exists(path):
        try:
            with open(path, "rb") as fh:
                cached = pickle.load(fh)
        except Exception:
            cached = {}

    # 관심종목이 늘면 유니버스 구성이 조금 바뀐다. 그때 500종목을 통째로
    # 다시 받지 않도록, 캐시에 없는 종목만 받아서 합친다.
    todo = [c for c in codes if c not in cached]
    if not todo:
        return {c: cached[c] for c in codes if c in cached}

    socket.setdefaulttimeout(TIMEOUT)
    start = (dt.date.today() - dt.timedelta(days=history_days)).isoformat()
    out: dict[str, pd.DataFrame] = {c: cached[c] for c in codes if c in cached}
    done, total, t0 = 0, len(todo), time.time()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_one, c, start) for c in todo]
        for fut in as_completed(futures):
            code, df = fut.result()
            done += 1
            if df is not None:
                out[code] = add_indicators(df)
            if progress and (done % 25 == 0 or done == total):
                progress(done, total, time.time() - t0)

    if out:
        _purge_old(tag)
        cached.update(out)          # 다음 실행을 위해 받은 것을 캐시에 누적한다
        with open(path, "wb") as fh:
            pickle.dump(cached, fh)
    return out
