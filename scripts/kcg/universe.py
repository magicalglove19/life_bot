"""스크리닝 대상 종목 유니버스 (KOSPI + KOSDAQ 전 종목).

KRX 전자공시(KIND)의 상장법인목록을 받아 쓴다. FinanceDataReader 의
StockListing 은 원본 소스가 자주 끊기므로 여기서는 쓰지 않는다.
"""

from __future__ import annotations

import io
import json
import os
import re
import time

import pandas as pd
import requests

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_URL = "https://kind.krx.co.kr/corpgeneral/corpList.do?method=download&marketType={}"
_MARKETS = {"KOSPI": "stockMkt", "KOSDAQ": "kosdaqMkt"}
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) kcg-screener/1.0"}
_CACHE_TTL = 7 * 24 * 3600      # 상장 종목은 자주 안 바뀐다

_PREFERRED = re.compile(r"(우|우B|우C|\d우)$")


def _cache_path() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, "universe_krx.json")


def _load_cache() -> list[dict] | None:
    path = _cache_path()
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < _CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _fetch(market: str) -> list[dict]:
    html = requests.get(_URL.format(_MARKETS[market]), headers=_HEADERS, timeout=30).text
    table = pd.read_html(io.StringIO(html))[0]
    rows = []
    for _, r in table.iterrows():
        code = str(r["종목코드"]).strip().zfill(6)
        if not code.isdigit():
            continue
        rows.append({
            "code": code,
            "name": str(r["회사명"]).strip(),
            "market": market,
            "sector": str(r.get("업종", "")).strip(),
        })
    return rows


def load(market: str = "ALL", use_cache: bool = True, exclude_spac: bool = True,
         exclude_preferred: bool = True, limit: int | None = None) -> list[dict]:
    """{'code','name','market','sector'} 딕셔너리 리스트를 반환한다."""
    rows = _load_cache() if use_cache else None
    if rows is None:
        rows = []
        for mk in _MARKETS:
            rows.extend(_fetch(mk))
        with open(_cache_path(), "w", encoding="utf-8") as fh:
            json.dump(rows, fh, ensure_ascii=False, indent=1)

    if market != "ALL":
        rows = [r for r in rows if r["market"] == market.upper()]
    if exclude_spac:
        rows = [r for r in rows if "스팩" not in r["name"]]
    if exclude_preferred:
        rows = [r for r in rows if not _PREFERRED.search(r["name"])]

    rows.sort(key=lambda r: r["code"])
    return rows[:limit] if limit else rows


def load_file(path: str) -> list[dict]:
    """'종목코드,종목명' 형식의 사용자 목록 파일."""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = [p.strip() for p in line.split(",")]
            code = parts[0].zfill(6)
            rows.append({
                "code": code,
                "name": parts[1] if len(parts) > 1 else code,
                "market": parts[2] if len(parts) > 2 else "",
                "sector": parts[3] if len(parts) > 3 else "",
            })
    return rows
