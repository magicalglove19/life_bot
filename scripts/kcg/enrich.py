"""종가배팅 후보에 재료(뉴스)·시총·외인/기관 수급을 붙인다 — 네이버 증권 모바일 API.

전 종목에 부르면 느리고 막히므로, 일봉 조건을 통과한 후보(수십 개 이하)에만 쓴다.
조회가 실패하면 값을 비워 두고 넘어간다. 재료 필터는 '조회에 성공했는데 뉴스가 없을 때'만
종목을 뺀다 — 네이버가 막혔다고 후보가 통째로 사라지면 안 된다.
"""

from __future__ import annotations

import datetime as dt
import re
from concurrent.futures import ThreadPoolExecutor

import requests

_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) kcg-screener/1.0"}
_NEWS = "https://m.stock.naver.com/api/news/stock/{code}?pageSize=10&page=1"
_INFO = "https://m.stock.naver.com/api/stock/{code}/integration"
KST = dt.timezone(dt.timedelta(hours=9))
TIMEOUT = 8


def news_since(now: dt.datetime) -> dt.datetime:
    """'오늘 재료'로 인정하는 시작 시각 — 직전 거래일(주말 건너뜀) 장 마감 15:30."""
    day = now.date() - dt.timedelta(days=1)
    while day.weekday() > 4:
        day -= dt.timedelta(days=1)
    return dt.datetime.combine(day, dt.time(15, 30), tzinfo=KST)


def _news(code: str, since: dt.datetime) -> list[tuple[str, str]] | None:
    try:
        r = requests.get(_NEWS.format(code=code), headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        groups = r.json()
    except Exception:
        return None
    out = []
    for g in groups:
        for it in g.get("items", []):
            try:
                t = dt.datetime.strptime(it["datetime"], "%Y%m%d%H%M").replace(tzinfo=KST)
            except (KeyError, ValueError):
                continue
            if t >= since:
                title = re.sub(r"<[^>]+>", "", it.get("titleFull") or it.get("title", ""))
                out.append((t.strftime("%H:%M"), title.strip()))
    return sorted(out, reverse=True)


def _num(text: str) -> float:
    s = str(text).replace(",", "").replace("+", "").strip()
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _marcap(text: str) -> float:
    """'1,517조 1,093억' → 원."""
    total = 0.0
    for val, unit in re.findall(r"([\d,]+)\s*(조|억)", str(text)):
        total += _num(val) * (1e12 if unit == "조" else 1e8)
    return total if total > 0 else float("nan")


def _info(code: str) -> dict | None:
    try:
        r = requests.get(_INFO.format(code=code), headers=_HEADERS, timeout=TIMEOUT)
        r.raise_for_status()
        d = r.json()
    except Exception:
        return None
    out = {}
    for t in d.get("totalInfos", []):
        if t.get("code") == "marketValue":
            out["marcap"] = _marcap(t.get("value", ""))
    trend = d.get("dealTrendInfos") or []
    if trend:
        out["foreign"] = _num(trend[0].get("foreignerPureBuyQuant", ""))
        out["organ"] = _num(trend[0].get("organPureBuyQuant", ""))
    return out


def enrich(picks: list, now: dt.datetime | None = None, workers: int = 4) -> None:
    now = now or dt.datetime.now(KST)
    since = news_since(now)

    def one(p):
        news = _news(p.code, since)
        if news is not None:
            p.news, p.news_checked = news, True
        info = _info(p.code)
        if info:
            p.marcap = info.get("marcap", p.marcap)
            p.foreign = info.get("foreign", p.foreign)
            p.organ = info.get("organ", p.organ)

    with ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, picks))
