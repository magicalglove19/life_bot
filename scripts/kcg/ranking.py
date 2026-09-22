"""유니버스를 좁히기 위한 순위 소스 (네이버 금융).

전 종목(2,600개)을 매번 받으면 서버가 동시 요청을 막아 오히려 느려진다.
그래서 이 전략에 실제로 의미 있는 종목만 추린다.

  1. 거래대금 상위 — 상한가·장대양봉은 돈이 몰린 곳에서 나온다
  2. 시가총액 상위 — 꾸준히 볼 만한 주력 종목
  3. 관심종목(watchlist) — 이미 트리거가 터져 추적 중인 종목은 무조건 포함
"""

from __future__ import annotations

import datetime as dt
import json
import os

import requests

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/125.0 Safari/537.36"),
    "Referer": "https://m.stock.naver.com/",
}
# finance.naver.com 의 시세 페이지(sise_quant / sise_market_sum)는 2026-09 부터
# 302 로 막혔다. 모바일 증권 API 는 열려 있고, 한 번에 종목별 거래대금·시총을 함께 준다.
_API = "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize={size}"
_MARKETS = ("KOSPI", "KOSDAQ")
_PAGE = 100
_MAX_PAGES = 30          # 100종목 × 30 = 시장당 3,000종목까지 (현재 코스피 2.5천/코스닥 1.8천)


def _cache_path() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"rank_{dt.date.today().isoformat()}.json")


def _fetch_market(market: str) -> list[tuple[str, float]]:
    """[(종목코드, 오늘 거래대금)] — API 응답 순서가 곧 시가총액 순이다."""
    out: list[tuple[str, float]] = []
    for page in range(1, _MAX_PAGES + 1):
        r = requests.get(_API.format(market=market, page=page, size=_PAGE),
                         headers=_HEADERS, timeout=15)
        r.raise_for_status()
        stocks = r.json().get("stocks", [])
        for st in stocks:
            code = str(st.get("itemCode", "")).strip()
            if len(code) != 6 or not code.isdigit():
                continue
            try:
                value = float(st.get("accumulatedTradingValueRaw") or 0)
            except (TypeError, ValueError):
                value = 0.0
            out.append((code, value))
        if len(stocks) < _PAGE:
            break
    return out


def fetch(marcap_pages: int = 8, use_cache: bool = True) -> dict[str, list[str]]:
    """{'value': 거래대금 상위, 'marcap': 시총 상위} — 당일 캐시.

    marcap_pages 는 옛 시그니처 호환용으로 남겨 두었다 (지금은 전 종목을 받는다).
    """
    path = _cache_path()
    if use_cache and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                cached = json.load(fh)
            if cached.get("value") or cached.get("marcap"):
                return cached
        except Exception:
            pass

    rows: list[tuple[str, float]] = []
    try:
        for market in _MARKETS:
            rows += _fetch_market(market)
    except Exception:
        pass                                       # 순위를 못 받으면 호출 쪽에서 전체로 되돌린다

    marcap = list(dict.fromkeys(code for code, _ in rows))
    value = [code for code, _ in sorted(rows, key=lambda x: x[1], reverse=True)]
    out = {"value": list(dict.fromkeys(value)), "marcap": marcap}
    if out["value"] or out["marcap"]:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False)
    return out


def pick(rows: list[dict], top: int, must_have: list[str] | None = None,
         use_cache: bool = True) -> list[dict]:
    """상장 종목 목록(rows)에서 우선순위 높은 순으로 top개만 남긴다.

    순서는 관심종목 → 거래대금 상위 → 시총 상위. top이 0 이하면 전체를 그대로 돌려준다.
    """
    if top <= 0:
        return rows

    by_code = {r["code"]: r for r in rows}
    ranked = fetch(use_cache=use_cache)
    order: list[str] = list(must_have or [])
    order += ranked.get("value", []) + ranked.get("marcap", [])

    picked: dict[str, dict] = {}
    for code in order:
        if code in by_code and code not in picked:
            picked[code] = by_code[code]
            if len(picked) >= top:
                break

    # 순위 소스가 죽으면 관심종목 몇 개만 남는다. 그 상태로 스캔하면 아무것도 안 나오므로
    # 차라리 전체를 본다 (느리지만 정확하다).
    if len(picked) < min(top, len(rows)) * 0.5:
        return rows
    return list(picked.values())
