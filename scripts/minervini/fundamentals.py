"""SEPA 펀더멘털 체크 — 미너비니의 'Code 33' 지향.

Code 33 = EPS·매출·순이익률이 3분기 연속 (전년동기 대비) 가속.

야후 파이낸스 무료 데이터는 분기 손익계산서를 보통 4~6분기만 준다.
전년동기 비교에는 최소 5분기(1개 비교), Code 33 완전판에는 7분기가 필요하므로
가능한 만큼만 계산하고 모자라면 '데이터부족'으로 표시한다(하드 필터로 쓰지 않는다).
"""

from __future__ import annotations

import os
import pickle
import time
import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import report, timing

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")

_REV_KEYS = ["Total Revenue", "Operating Revenue"]
_EPS_KEYS = ["Diluted EPS", "Basic EPS"]
_NI_KEYS = ["Net Income Common Stockholders", "Net Income", "Net Income Continuous Operations"]


@dataclass
class Fundamentals:
    ticker: str
    eps_yoy: float = np.nan          # 최근 분기 EPS 전년동기비 %
    sales_yoy: float = np.nan        # 최근 분기 매출 전년동기비 %
    margin_now: float = np.nan       # 최근 분기 순이익률 %
    margin_yoy_delta: float = np.nan  # 순이익률 전년동기 대비 변화 (%p)
    eps_yoy_series: list = field(default_factory=list)
    sales_yoy_series: list = field(default_factory=list)
    margin_series: list = field(default_factory=list)
    code33: str = "데이터부족"       # 충족 / 부분충족(n) / 미충족 / 데이터부족
    quarters: int = 0
    market_cap: float = np.nan
    sector: str = ""
    industry: str = ""
    error: str = ""

    @property
    def score(self) -> float:
        """0~100 펀더멘털 점수 (없는 값은 중립 처리)."""
        pts, total = 0.0, 0.0
        if np.isfinite(self.eps_yoy):
            total += 40
            pts += 40 * min(1.0, max(0.0, self.eps_yoy / 50.0))
        if np.isfinite(self.sales_yoy):
            total += 30
            pts += 30 * min(1.0, max(0.0, self.sales_yoy / 25.0))
        if np.isfinite(self.margin_yoy_delta):
            total += 20
            pts += 20 * min(1.0, max(0.0, (self.margin_yoy_delta + 2) / 6.0))
        total += 10
        pts += {"충족": 10.0, "미충족": 0.0}.get(self.code33, 5.0 if self.code33.startswith("부분") else 5.0)
        return round(pts / total * 100, 1) if total else 50.0


def _pick(df: pd.DataFrame, keys: list[str]) -> pd.Series | None:
    for k in keys:
        if k in df.index:
            s = pd.to_numeric(df.loc[k], errors="coerce")
            if s.notna().sum() >= 2:
                return s
    return None


def _yoy_series(s: pd.Series, n: int = 4) -> list[float]:
    """분기 시리즈(최신이 앞)에서 전년동기비 성장률 % 리스트(최신순)."""
    vals = s.to_numpy(dtype=float)
    out = []
    for i in range(len(vals) - n):
        cur, prev = vals[i], vals[i + n]
        if not (np.isfinite(cur) and np.isfinite(prev)) or prev == 0:
            out.append(np.nan)
        elif prev < 0:
            out.append(np.nan)  # 적자 → 흑자 전환은 성장률로 표현이 안 된다
        else:
            out.append((cur / prev - 1.0) * 100.0)
    return out


def _accelerating(series: list[float], n: int) -> bool:
    """최신 n개가 순차적으로 커지는가 (최신순 리스트 기준)."""
    vals = series[:n]
    if len(vals) < n or any(not np.isfinite(v) for v in vals):
        return False
    return all(vals[i] > vals[i + 1] for i in range(n - 1))


def fetch_one(ticker: str, quarters_required: int = 3) -> Fundamentals:
    import yfinance as yf

    f = Fundamentals(ticker=ticker)
    try:
        tk = yf.Ticker(ticker)
        q = tk.quarterly_income_stmt
        if q is None or q.empty:
            f.error = "손익계산서 없음"
        else:
            q = q.reindex(sorted(q.columns, reverse=True), axis=1)  # 최신이 앞
            f.quarters = q.shape[1]
            rev = _pick(q, _REV_KEYS)
            eps = _pick(q, _EPS_KEYS)
            ni = _pick(q, _NI_KEYS)

            if eps is not None:
                f.eps_yoy_series = _yoy_series(eps)
            if rev is not None:
                f.sales_yoy_series = _yoy_series(rev)
            if rev is not None and ni is not None:
                margin = (pd.to_numeric(ni, errors="coerce") / pd.to_numeric(rev, errors="coerce") * 100.0)
                f.margin_series = [float(x) for x in margin.to_numpy(dtype=float)]

            f.eps_yoy = f.eps_yoy_series[0] if f.eps_yoy_series else np.nan
            f.sales_yoy = f.sales_yoy_series[0] if f.sales_yoy_series else np.nan
            if f.margin_series:
                f.margin_now = f.margin_series[0]
                if len(f.margin_series) > 4 and np.isfinite(f.margin_series[4]):
                    f.margin_yoy_delta = f.margin_series[0] - f.margin_series[4]

            margin_yoy = _yoy_series(pd.Series(f.margin_series)) if len(f.margin_series) > 4 else []
            usable = min(
                len(f.eps_yoy_series), len(f.sales_yoy_series), len(margin_yoy) if margin_yoy else 0
            )
            if usable == 0:
                f.code33 = "데이터부족"
            else:
                n = min(quarters_required, usable)
                hit = (
                    _accelerating(f.eps_yoy_series, n)
                    and _accelerating(f.sales_yoy_series, n)
                    and _accelerating(margin_yoy, n)
                )
                if not hit:
                    f.code33 = "미충족"
                elif n >= quarters_required:
                    f.code33 = "충족"
                else:
                    f.code33 = f"부분충족({n}분기)"

        try:
            info = tk.get_info()
            f.sector = info.get("sector", "") or ""
            f.industry = info.get("industry", "") or ""
            mc = info.get("marketCap")
            f.market_cap = float(mc) if mc else np.nan
        except Exception:
            pass
    except Exception as exc:
        f.error = str(exc)[:120]
    return f


def fetch_many(
    tickers: list[str], quarters_required: int = 3, workers: int = 8, use_cache: bool = True, verbose: bool = True
) -> dict[str, Fundamentals]:
    os.makedirs(CACHE_DIR, exist_ok=True)
    cache_path = os.path.join(CACHE_DIR, f"fundamentals_{dt.date.today().isoformat()}.pkl")
    cache: dict[str, Fundamentals] = {}
    if use_cache and os.path.exists(cache_path):
        try:
            with open(cache_path, "rb") as fh:
                cache = pickle.load(fh)
        except Exception:
            cache = {}

    todo = [t for t in tickers if t not in cache]
    if todo:
        t0 = time.time()
        done = 0
        if verbose:
            report.progress(0, len(todo), 0.0, f"{report.DIM}실적 조회{report.RESET}")
        with ThreadPoolExecutor(max_workers=workers) as ex:
            for f in ex.map(lambda t: fetch_one(t, quarters_required), todo):
                cache[f.ticker] = f
                done += 1
                if verbose and (done % 5 == 0 or done == len(todo)):
                    report.progress(done, len(todo), time.time() - t0, f"{report.DIM}실적 조회{report.RESET}")
        elapsed = time.time() - t0
        if len(todo) >= 20 and elapsed > 0.5:
            timing.update("fund_per_ticker", elapsed / len(todo))
        if verbose:
            report.progress_done(elapsed, f"펀더멘털 {len(todo)}종목 조회 (캐시 {len(tickers) - len(todo)})")
        if use_cache:
            with open(cache_path, "wb") as fh:
                pickle.dump(cache, fh)

    return {t: cache[t] for t in tickers if t in cache}
