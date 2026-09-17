"""스크리닝 파이프라인 오케스트레이션."""

from __future__ import annotations

import dataclasses
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
from . import indicators
from .indicators import percentile_rating, rs_score
from .universe import INDEX_LABEL, load_universe

# 시장 폭(breadth)을 잴 때 쓰는 고정 RS 기준.
# 시장 국면은 '시장이 어떤 상태인가'를 재는 것이지 '내 필터가 몇 개를 남기는가'가 아니다.
# cfg.trend.min_rs_rating 을 올리면 통과 종목이 줄어드는데, 그걸 그대로 시장 폭으로 쓰면
# 필터를 조일 때마다 신호등이 저절로 빨개진다. 그래서 여기만 원문 기준(70)으로 고정한다.
BREADTH_RS = 70.0

# 종합점수 가중치 (펀더멘털이 없으면 나머지로 재분배)
WEIGHTS = {"rs": 0.35, "vcp": 0.35, "trend": 0.15, "fund": 0.15}


@dataclass
class Candidate:
    ticker: str
    name: str = ""
    sector: str = ""
    size: str = ""             # 대형/중형/소형 (소속 지수 기준)
    price: float = np.nan
    rs_rating: float = np.nan
    rs_raw: float = np.nan
    pct_from_high: float = np.nan
    pct_from_low: float = np.nan
    dollar_vol: float = np.nan
    trend: object = None
    vcp: vcp.VCPResult = field(default_factory=vcp.VCPResult)
    fundamentals: fund.Fundamentals | None = None
    ud_ratio: float = np.nan   # 기관 매집 — 상승일 거래량 ÷ 하락일 거래량 (최근 50봉)
    ud_ok: bool = False
    growth_ok = None           # 실적 급증 기준 통과 (True/False/None=데이터 없음)
    growth_note: str = ""      # "EPS +35% · 매출 +31%" 또는 "데이터 없음"
    drop_reason: str = ""      # 실적·매집 기준으로 빠졌다면 그 이유
    stage2_since = None      # Trend Template을 연속 통과하기 시작한 날
    stage2_days: int = 0
    setup_since = None       # VCP 셋업이 잡히기 시작한 날
    setup_days: int = 0
    days_past_pivot: int = -1   # 피벗을 넘은 뒤 지난 거래일 수 (-1이면 아직 피벗 아래)
    pct_above_pivot: float = np.nan  # 현재가가 피벗보다 몇 % 위인가 (피벗 아래면 NaN)
    extended: bool = False      # 연장 = 날짜 또는 거리로 타점을 지나쳤다
    extended_reason: str = ""   # "7일 경과" / "피벗 +10.2%"
    high52_date = None
    entry: float = np.nan
    stop: float = np.nan
    stop_pct: float = np.nan
    shares: int = 0
    position_value: float = np.nan
    risk_amount: float = np.nan
    risk_mult: float = 1.0      # 이 자리에 건 1회 리스크 배수 (돌파 1.0 / 미확인 0.5)
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
    last_bar: str = ""       # 판정의 기준이 된 마지막 거래일 (YYYY-MM-DD)
    frames: dict = field(default_factory=dict, repr=False)   # {티커: OHLCV} — 다른 전략(쿨라매기)이 재사용
    names: dict = field(default_factory=dict, repr=False)    # {티커: (종목명, 섹터)}
    quality_dropped: list = field(default_factory=list)      # 실적·매집 기준으로 빠진 종목


def mark_quality(c: "Candidate", cfg: Config) -> None:
    """미너비니가 기술적 타점 전에 확인하는 두 가지 — 실적 급증과 기관 매집."""
    f = cfg.fundamental
    c.ud_ok = bool(np.isfinite(c.ud_ratio) and c.ud_ratio >= f.ud_min)

    fu = c.fundamentals
    if f.mode == "off" or fu is None or fu.error:
        c.growth_ok, c.growth_note = None, "데이터 없음"
        return
    eps, sales = fu.eps_yoy, fu.sales_yoy
    if not (np.isfinite(eps) and np.isfinite(sales)):
        c.growth_ok, c.growth_note = None, "데이터 없음"
        return
    c.growth_ok = bool(eps >= f.min_eps_growth and sales >= f.min_sales_growth)
    c.growth_note = f"EPS {eps:+.0f}% · 매출 {sales:+.0f}%"


def _quality_filtered(cands: list, cfg: Config) -> tuple:
    """실적·매집 기준으로 걸러낸다. (남은 목록, 제외된 목록)"""
    f = cfg.fundamental
    keep, dropped = [], []
    for c in cands:
        out = []
        if f.mode == "filter":
            if c.growth_ok is False:
                out.append("실적 미달")
            elif c.growth_ok is None and f.require_data:
                out.append("실적 데이터 없음")
        if f.ud_mode == "filter" and not c.ud_ok:
            out.append(f"매집 {c.ud_ratio:.2f}" if np.isfinite(c.ud_ratio) else "매집 없음")
        if out:
            c.drop_reason = " · ".join(out)
            dropped.append(c)
        else:
            keep.append(c)
    return keep, dropped


def mark_extended(c: "Candidate", cfg: Config) -> None:
    """매수 타점을 지나쳤는지 — 날짜(며칠 지났나)와 거리(얼마나 올랐나) 둘 다 본다."""
    c.pct_above_pivot = np.nan
    c.extended, c.extended_reason = False, ""
    if c.days_past_pivot < 0:
        return
    pivot = c.vcp.pivot
    if np.isfinite(pivot) and pivot > 0 and np.isfinite(c.price):
        c.pct_above_pivot = (c.price / pivot - 1.0) * 100.0
    reasons = []
    if c.days_past_pivot > cfg.vcp.max_days_past_pivot:
        reasons.append(f"{c.days_past_pivot}일 경과")
    if np.isfinite(c.pct_above_pivot) and round(c.pct_above_pivot, 6) > cfg.vcp.max_pct_above_pivot:
        reasons.append(f"피벗 +{c.pct_above_pivot:.1f}%")
    c.extended = bool(reasons)
    c.extended_reason = " · ".join(reasons)


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


def risk_mult(c: Candidate, cfg: Config) -> float:
    """이 자리에 1회 리스크를 얼마나 걸 것인가 (config.RiskConfig 주석 참고).

    거래량으로 확인된 돌파와, 아직 피벗을 못 넘은 매수구간은 성적이 크게 달랐다.
    같은 금액을 거는 게 오히려 이상하다.
    """
    confirmed = c.vcp.is_vcp and c.vcp.status == "돌파"
    return cfg.risk.breakout_risk_mult if confirmed else cfg.risk.setup_risk_mult


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
    c.risk_mult = risk_mult(c, cfg)
    budget = risk.account_size * risk.risk_per_trade_pct / 100.0 * c.risk_mult
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
    size_of = dict(zip(uni["ticker"], uni["index"])) if "index" in uni.columns else {}

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
                size=INDEX_LABEL.get(size_of.get(tk, ""), ""),
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
        c.ud_ratio = indicators.up_down_volume_ratio(frames[c.ticker], cfg.fundamental.ud_window)
        if c.vcp.breakout_date is not None:
            idx = frames[c.ticker].index
            try:
                c.days_past_pivot = len(idx) - idx.get_loc(c.vcp.breakout_date) - 1
            except KeyError:
                c.days_past_pivot = -1
        mark_extended(c, cfg)

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
        mark_quality(c, cfg)
    quality_dropped = []
    if cfg.fundamental.mode == "filter" or cfg.fundamental.ud_mode == "filter":
        candidates, quality_dropped = _quality_filtered(candidates, cfg)
        if verbose and quality_dropped:
            report.progress_done(0.0, f"실적·매집 기준으로 {report.BOLD}{len(quality_dropped)}{report.RESET}종목 제외 "
                                      f"{report.DIM}(남은 {len(candidates)}){report.RESET}")

    for c in candidates:
        _position_sizing(c, cfg)
        c.total_score = _composite(c)

    candidates.sort(key=lambda x: x.total_score, reverse=True)

    # 시장 폭은 사용자의 RS 하한과 무관하게 고정 기준으로 센다 (위 BREADTH_RS 주석 참고)
    n = max(1, len(metrics))
    breadth_trend = dataclasses.replace(cfg.trend, min_rs_rating=BREADTH_RS)
    n_breadth = sum(
        1 for tk, m in metrics.items()
        if trend_template.evaluate(m, float(ratings.get(tk, np.nan)), breadth_trend).ok
    )
    regime = market.analyze(bench, n_breadth / n * 100.0, above_ma200 / n * 100.0, cfg.benchmark)

    # 저널·리뷰가 '언제 기준 신호인지'를 알 수 있도록 마지막 거래일을 남긴다
    last_bar = ""
    if bench is not None and len(bench):
        last_bar = pd.Timestamp(bench.index[-1]).strftime("%Y-%m-%d")
    elif frames:
        last_bar = pd.Timestamp(max(f.index[-1] for f in frames.values())).strftime("%Y-%m-%d")

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
        last_bar=last_bar,
        frames=frames,
        names=meta,
        quality_dropped=quality_dropped,
    )


def to_dataframe(candidates: list[Candidate], ud_strong: float = 2.0) -> pd.DataFrame:
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
                "규모": c.size or "-",
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
                "피벗위%": f"{c.pct_above_pivot:+.1f}%" if np.isfinite(c.pct_above_pivot) else "-",
                "연장": c.extended_reason or "-",
                # 실행
                "피벗": f"{v.pivot:,.2f}" if ok else "-",
                "진입가": f"{c.entry:,.2f}" if np.isfinite(c.entry) else "-",
                "손절가": f"{c.stop:,.2f}" if np.isfinite(c.stop) else "-",
                "손절%": f"{c.stop_pct:.1f}%" if np.isfinite(c.stop_pct) else "-",
                "수량": c.shares or "-",
                "투자금액": f"{c.position_value:,.0f}" if np.isfinite(c.position_value) else "-",
                "비중": f"{c.risk_mult:.1f}x",
                # 패턴 상세
                "수축": " → ".join(f"{d:.0f}%" for d in v.depths) if (ok and v.depths) else "-",
                "베이스(봉)": v.base_bars if ok else "-",
                "베이스시작": history.fmt(v.base_start_date) if ok else "-",
                "피벗형성": history.fmt(v.pivot_date) if ok else "-",
                "돌파일": history.fmt(v.breakout_date) if v.breakout_date is not None else "-",
                "돌파거래량": f"{v.breakout_volume_mult:.2f}x" if np.isfinite(v.breakout_volume_mult) else "-",
                "52주고점일": history.fmt(c.high52_date),
                "거래량마름": f"{v.dryup_ratio:.2f}" if (ok and np.isfinite(v.dryup_ratio)) else "-",
                # 펀더멘털
                "실적기준": {True: "통과", False: "미달"}.get(c.growth_ok, "데이터없음"),
                "U/D": f"{c.ud_ratio:.2f}" if np.isfinite(c.ud_ratio) else "-",
                "매집": "강함" if (np.isfinite(c.ud_ratio) and c.ud_ratio >= ud_strong) else ("있음" if c.ud_ok else "약함"),
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
