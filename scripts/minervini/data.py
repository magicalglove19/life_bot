"""야후 파이낸스에서 일봉 데이터를 내려받고 캐싱한다."""

from __future__ import annotations

import datetime as dt
import os
import pickle
import time
import warnings

import pandas as pd

from . import report, timing

warnings.filterwarnings("ignore", category=FutureWarning)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_REQUIRED = ["Open", "High", "Low", "Close", "Volume"]


def _cache_file(tag: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    today = dt.date.today().isoformat()
    return os.path.join(CACHE_DIR, f"prices_{tag}_{today}.pkl")


def _purge_old(tag: str, keep_days: int = 5) -> None:
    """오래된 가격 캐시 정리."""
    cutoff = dt.date.today() - dt.timedelta(days=keep_days)
    if not os.path.isdir(CACHE_DIR):
        return
    for fn in os.listdir(CACHE_DIR):
        if not (fn.startswith(f"prices_{tag}_") and fn.endswith(".pkl")):
            continue
        try:
            stamp = dt.date.fromisoformat(fn[len(f"prices_{tag}_"):-4])
        except ValueError:
            continue
        if stamp < cutoff:
            os.remove(os.path.join(CACHE_DIR, fn))


def _split_frames(raw: pd.DataFrame, tickers: list[str]) -> dict[str, pd.DataFrame]:
    """yfinance의 MultiIndex 결과를 {티커: OHLCV DataFrame}으로 분해."""
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out

    if isinstance(raw.columns, pd.MultiIndex):
        level0 = set(raw.columns.get_level_values(0))
        for tk in tickers:
            if tk not in level0:
                continue
            sub = raw[tk]
            if not set(_REQUIRED).issubset(sub.columns):
                continue
            sub = sub[_REQUIRED].dropna(how="all")
            if not sub.empty:
                out[tk] = sub
    elif len(tickers) == 1 and set(_REQUIRED).issubset(raw.columns):
        sub = raw[_REQUIRED].dropna(how="all")
        if not sub.empty:
            out[tickers[0]] = sub
    return out


def download_prices(
    tickers: list[str],
    history_days: int = 500,
    batch_size: int = 100,
    use_cache: bool = True,
    tag: str = "main",
    verbose: bool = True,
) -> dict[str, pd.DataFrame]:
    """티커별 일봉(OHLCV, 수정주가) DataFrame 딕셔너리를 반환한다."""
    import yfinance as yf

    cache_path = _cache_file(tag)
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as fh:
                cached = pickle.load(fh)
            # 같은 날, 같은 요청 목록이면 그대로 재사용 (상장폐지 등으로 못 받은 종목 포함)
            if set(tickers).issubset(set(cached.get("requested", []))):
                if verbose:
                    print(f"  {report.GREEN}✔{report.RESET} 오늘자 캐시 재사용 {report.DIM}({os.path.basename(cache_path)}){report.RESET}")
                return {t: f for t, f in cached["frames"].items() if t in set(tickers)}
        except Exception:
            pass  # 캐시가 깨졌으면 그냥 새로 받는다

    # 달력일 기준으로 넉넉히 (거래일 ≈ 달력일 * 0.69)
    start = dt.date.today() - dt.timedelta(days=int(history_days * 1.5) + 10)
    frames: dict[str, pd.DataFrame] = {}
    total = len(tickers)
    t0 = time.time()

    for i in range(0, total, batch_size):
        batch = tickers[i : i + batch_size]
        if verbose:
            report.progress(i, total, time.time() - t0, f"{report.DIM}야후 파이낸스{report.RESET}")
        try:
            raw = yf.download(
                batch,
                start=start.isoformat(),
                auto_adjust=True,
                group_by="ticker",
                threads=True,
                progress=False,
                actions=False,
            )
        except Exception as exc:  # 네트워크/야후 오류는 배치 단위로 넘어간다
            print(f"\n    {report.YELLOW}[경고] 배치 실패: {exc}{report.RESET}")
            continue
        frames.update(_split_frames(raw, batch))

    elapsed = time.time() - t0
    if total >= 50 and elapsed > 0.5:  # 소량 조회는 오버헤드 비중이 커서 표본으로 못 쓴다
        timing.update("download_per_100", elapsed / total * 100)

    if use_cache and frames:
        with open(cache_path, "wb") as fh:
            pickle.dump({"requested": list(tickers), "frames": frames}, fh)
        _purge_old(tag)

    if verbose:
        report.progress_done(elapsed, f"가격 데이터 {len(frames)}/{total}종목 수집")
    return frames
