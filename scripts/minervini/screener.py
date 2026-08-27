"""스크리닝 파이프라인 오케스트레이션."""

from __future__ import annotations

import datetime as dt
import math
import os
import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import fundamentals as fund
from . import history, market, report, timing, trend_template, vcp
from .config import Config
from .data import _cache_file, download_prices
from .indicators import percentile_rating, rs_score
from .universe import load_universe

# 종합점수 가중치 (펀더멘털이 없으면 나머지로 재분배)
WEIGHTS = {"rs": 0.35, "vcp": 0.35, "trend": 0.15, "fund": 0.15}


@dataclass
class Candidate:
    ticker: str
    name: str = ""
    sector: str = ""
    price: float = np.nan
    rs_rating: float = np.nan
    rs_raw: float = np.nan
    pct_from_high: float = np.nan
    pct_from_low: float = np.nan
    dollar_vol: float = np.nan
    trend: object = None
    vcp: vcp.VCPResult = field(default_factory=vcp.VCPResult)
    fundamentals: fund.Fundamentals | None = None
    stage2_since = None      # Trend Template을 연속 통과하기 시작한 날
    stage2_days: int = 0
    setup_since = None       # VCP 셋업이 잡히기 시작한 날
    setup_days: int = 0
    days_past_pivot: int = -1   # 피벗을 넘은 뒤 지난 거래일 수 (-1이면 아직 피벗 아래)
    high52_date = None
    entry: float = np.nan
    stop: float = np.nan
    stop_pct: float = np.nan
    shares: int = 0
    position_value: float = np.nan
    risk_amount: float = np.nan
    total_score: float = 0.0


@dataclass
class ScanResult:
    candidates: list[Candidate]
    stage2: list[Candidate]
    regime: market.MarketRegime
    scanned: int
    universe_name: str
    generated: str
    elapsed: float = 0.0


def _trend_strength(c: Candidate) -> float:
    """52주 고점 근접도 기반 추세 강도 0~100 (고점 = 100, -25% = 0)."""
    if not np.isfinite(c.pct_from_high):
        return 0.0
    return float(np.clip(100 + c.pct_from_high * 4, 0, 100))


def _composite(c: Candidate) -> float:
    parts = {"rs": c.rs_rating, "vcp": c.vcp.score if c.vcp.is_vcp else 0.0, "trend": _trend_strength(c)}
    weights = dict(WEIGHTS)
    if c.fundamentals is not None and not c.fundamentals.error:
        parts["fund"] = c.fundamentals.score
    else:
        w = weights.pop("fund")
        for k in weights:
            weights[k] += w * (WEIGHTS[k] / (1 - WEIGHTS["fund"]))
    total = sum(parts[k] * weights[k] for k in parts if np.isfinite(parts.get(k, np.nan)))
    return round(total, 1)


def _position_sizing(c: Candidate, cfg: Config) -> None:
    """미너비니식 역산: 손절폭에서 수량을 정한다 (금액이 아니라 리스크가 먼저)."""
    risk = cfg.risk
    price = c.price
    if not np.isfinite(price) or price <= 0:
        return

    # 유효한 VCP가 아니면 피벗/구조적 손절을 신뢰하지 않는다
    pivot = c.vcp.pivot if (c.vcp.is_vcp and np.isfinite(c.vcp.pivot)) else np.nan
    # 이미 피벗 위면 현재가가 진입가, 아직이면 피벗 바로 위에 매수 스톱
    c.entry = round(max(price, pivot * 1.001), 2) if np.isfinite(pivot) else round(price, 2)

    max_stop_price = c.entry * (1 - risk.max_stop_pct / 100.0)
    structural = c.vcp.stop if (c.vcp.is_vcp and np.isfinite(c.vcp.stop)) else np.nan
    c.stop = round(max(structural, max_stop_price) if np.isfinite(structural) else max_stop_price, 2)
    c.stop_pct = (c.entry - c.stop) / c.entry * 100.0

    per_share_risk = c.entry - c.stop
    if per_share_risk <= 0:
        return
    budget = risk.account_size * risk.risk_per_trade_pct / 100.0
    shares = math.floor(budget / per_share_risk)
    cap = math.floor(risk.account_size * risk.max_position_pct / 100.0 / c.entry)
    c.shares = max(0, min(shares, cap))
    c.position_value = round(c.shares * c.entry, 2)
    c.risk_amount = round(c.shares * per_share_risk, 2)


def scan(
    cfg: Config,
    universe_name: str = "sp500",
    custom_file: str | None = None,
    limit: int | None = None,
    use_cache: bool = True,
    with_fundamentals: bool = True,
    vcp_only: bool = False,
    verbose: bool = True,
) -> ScanResult:
    t_start = time.time()
    uni = load_universe(universe_name, custom_file, limit)
    tickers = uni["ticker"].tolist()
    meta = {r.ticker: (r.name, r.sector) for r in uni.itertuples()}

    # 캐시 키: 유니버스별로 분리해야 서로 덮어쓰지 않는다
    if custom_file:
        tag = "file_" + os.path.splitext(os.path.basename(custom_file))[0]
    else:
        tag = f"{universe_name}{'_' + str(limit) if limit else ''}"

    cached = use_cache and os.path.exists(_cache_file(tag))
    fund_cached = use_cache and os.path.exists(
        os.path.join(fund.CACHE_DIR, f"fundamentals_{dt.date.today().isoformat()}.pkl")
    )
    if verbose:
        est = timing.estimate(len(tickers), cached, with_fundamentals, fund_cached)
        print()
        print(f"  {report.BOLD}유니버스{report.RESET} {universe_name} · {report.BOLD}{len(tickers)}종목{report.RESET}"
              f"   {report.DIM}|{report.RESET}   {report.BOLD}예상 소요 시간{report.RESET} "
              f"{report.CYAN}{report.BOLD}약 {timing.human(est)}{report.RESET}"
              + (f" {report.DIM}(캐시 사용){report.RESET}" if cached else ""))
        print(report.rule("─", 78))
        print(f"  {report.DIM}1/4{report.RESET} 가격 데이터 수집 {report.DIM}(최근 {cfg.history_days} 거래일){report.RESET}")

    frames = download_prices(tickers + [cfg.benchmark], cfg.history_days, use_cache=use_cache, tag=tag, verbose=verbose)
    bench = frames.get(cfg.benchmark)

    t_analysis = time.time()
    if verbose:
        print(f"  {report.DIM}2/4{report.RESET} Trend Template 8개 기준 판정")

    # --- 지표 계산 + 유동성 필터 ---
    metrics, raw_scores = {}, {}
    above_ma200 = 0
    for tk in tickers:
        df = frames.get(tk)
        if df is None or len(df) < 60:
            continue
        m = trend_template.compute_metrics(tk, df, cfg.trend)
        if m is None:
            continue
        if m["price"] < cfg.min_price or m["dollar_vol50"] < cfg.min_avg_dollar_volume:
            continue
        metrics[tk] = m
        raw_scores[tk] = rs_score(df["Close"].dropna(), cfg.rs.periods, cfg.rs.weights)
        if m["price"] > m["ma200"]:
            above_ma200 += 1

    ratings = percentile_rating(pd.Series(raw_scores))

    # --- Trend Template 판정 ---
    stage2: list[Candidate] = []
    for tk, m in metrics.items():
        rating = float(ratings.get(tk, np.nan))
        tr = trend_template.evaluate(m, rating, cfg.trend)
        if not tr.ok:
            continue
        name, sector = meta.get(tk, (tk, ""))
        stage2.append(
            Candidate(
                ticker=tk,
                name=name,
                sector=sector,
                price=m["price"],
                rs_rating=rating,
                rs_raw=raw_scores[tk],
                pct_from_high=m["pct_from_high"],
                pct_from_low=m["pct_from_low"],
                dollar_vol=m["dollar_vol50"],
                trend=tr,
            )
        )

    if verbose:
        report.progress_done(0.0, f"Stage 2 상승추세 {report.BOLD}{len(stage2)}{report.RESET}종목 통과 "
                                  f"{report.DIM}(스캔 {len(metrics)}){report.RESET}")
        print(f"  {report.DIM}3/4{report.RESET} VCP 패턴 탐지")

    for c in stage2:
        c.vcp = vcp.detect(frames[c.ticker], cfg.vcp)
        c.high52_date = c.trend.metrics.get("high52_date")
        if c.vcp.breakout_date is not None:
            idx = frames[c.ticker].index
            try:
                c.days_past_pivot = len(idx) - idx.get_loc(c.vcp.breakout_date) - 1
            except KeyError:
                c.days_past_pivot = -1

    # 과거 시점으로 되감아 "언제부터인가"를 역산한다
    if stage2:
        closes = history.build_close_matrix(frames, list(metrics.keys()))
        ratings_hist = history.rs_rating_matrix(closes, cfg.rs.periods, cfg.rs.weights)
        for c in stage2:
            df = frames[c.ticker]
            flags = history.trend_template_series(df, ratings_hist[c.ticker], cfg)
            c.stage2_since, c.stage2_days = history.streak(flags)
            if c.vcp.is_vcp:
                c.setup_since, c.setup_days = history.vcp_streak(df, cfg)

    candidates = [c for c in stage2 if c.vcp.is_vcp] if vcp_only else list(stage2)
    if len(tickers) >= 50 and (time.time() - t_analysis) > 0.2:
        timing.update("analysis_per_100", (time.time() - t_analysis) / len(tickers) * 100)

    if verbose:
        n_vcp = sum(1 for c in stage2 if c.vcp.is_vcp)
        n_act = sum(1 for c in stage2 if c.vcp.status in ("매수구간", "돌파"))
        report.progress_done(0.0, f"VCP 셋업 {report.BOLD}{n_vcp}{report.RESET}종목 "
                                  f"{report.DIM}(매수구간/돌파 {n_act}){report.RESET}")

    if with_fundamentals and candidates:
        if verbose:
            print(f"  {report.DIM}4/4{report.RESET} 펀더멘털(Code 33) 확인")
        fmap = fund.fetch_many([c.ticker for c in candidates], cfg.fundamental.quarters_required, use_cache=use_cache, verbose=verbose)
        for c in candidates:
            c.fundamentals = fmap.get(c.ticker)
            if c.fundamentals and c.fundamentals.sector and not c.sector:
                c.sector = c.fundamentals.sector
    elif verbose:
        print(f"  {report.DIM}4/4 펀더멘털 조회 생략{report.RESET}")

    for c in candidates:
        _position_sizing(c, cfg)
        c.total_score = _composite(c)

    candidates.sort(key=lambda x: x.total_score, reverse=True)

    n = max(1, len(metrics))
    regime = market.analyze(bench, len(stage2) / n * 100.0, above_ma200 / n * 100.0, cfg.benchmark)

    total_elapsed = time.time() - t_start
    if verbose:
        print(report.rule("─", 78))
        print(f"  {report.GREEN}{report.BOLD}완료{report.RESET}  총 소요 {report.BOLD}{timing.human(total_elapsed)}{report.RESET}")

    return ScanResult(
        candidates=candidates,
        stage2=stage2,
        regime=regime,
        scanned=len(metrics),
        universe_name=universe_name,
        generated=dt.datetime.now().strftime("%Y-%m-%d %H:%M"),
        elapsed=total_elapsed,
    )


def to_dataframe(candidates: list[Candidate]) -> pd.DataFrame:
    rows = []
    for i, c in enumerate(candidates, 1):
        f = c.fundamentals
        v = c.vcp
        ok = v.is_vcp
        rows.append(
            {
                # 종목
                "순위": i,
                "티커": c.ticker,
                "종목명": c.name,
                "섹터": c.sector,
                "현재가": f"{c.price:,.2f}",
                "RS등급": int(c.rs_rating) if np.isfinite(c.rs_rating) else "",
                "52주고점대비": f"{c.pct_from_high:+.1f}%",
                # 상태와 신선도
                "VCP상태": v.status,
                "셋업일수": v.is_vcp and c.setup_days or "-",
                "셋업등장": history.fmt(c.setup_since) if c.setup_since is not None else "-",
                "Stage2일수": c.stage2_days or "-",
                "Stage2진입": history.fmt(c.stage2_since) if c.stage2_since is not None else "-",
                "타점경과": ("오늘" if c.days_past_pivot == 0 else f"{c.days_past_pivot}일") if c.days_past_pivot >= 0 else "-",
                # 실행
                "피벗": f"{v.pivot:,.2f}" if ok else "-",
                "진입가": f"{c.entry:,.2f}" if np.isfinite(c.entry) else "-",
                "손절가": f"{c.stop:,.2f}" if np.isfinite(c.stop) else "-",
                "손절%": f"{c.stop_pct:.1f}%" if np.isfinite(c.stop_pct) else "-",
                "수량": c.shares or "-",
                "투자금액": f"{c.position_value:,.0f}" if np.isfinite(c.position_value) else "-",
                # 패턴 상세
                "수축": " → ".join(f"{d:.0f}%" for d in v.depths) if (ok and v.depths) else "-",
                "베이스(봉)": v.base_bars if ok else "-",
                "베이스시작": history.fmt(v.base_start_date) if ok else "-",
                "피벗형성": history.fmt(v.pivot_date) if ok else "-",
                "돌파일": history.fmt(v.breakout_date) if v.breakout_date is not None else "-",
                "52주고점일": history.fmt(c.high52_date),
                "거래량마름": f"{v.dryup_ratio:.2f}" if (ok and np.isfinite(v.dryup_ratio)) else "-",
                # 펀더멘털
                "EPS성장%": f"{f.eps_yoy:+.0f}%" if f and np.isfinite(f.eps_yoy) else "-",
                "매출성장%": f"{f.sales_yoy:+.0f}%" if f and np.isfinite(f.sales_yoy) else "-",
                "Code33": f.code33 if f else "-",
                # 점수
                "VCP점수": v.score if ok else 0.0,
                "종합점수": c.total_score,
                "비고": "" if ok else (v.note or "VCP 미형성"),
            }
        )
    return pd.DataFrame(rows)
