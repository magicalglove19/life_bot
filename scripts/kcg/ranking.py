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
import re

import requests

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) kcg-screener/1.0"}
_QUANT = "https://finance.naver.com/sise/sise_quant.naver?sosok={}"
_MARCAP = "https://finance.naver.com/sise/sise_market_sum.naver?sosok={}&page={}"
_CODE = re.compile(r"code=(\d{6})")


def _cache_path() -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"rank_{dt.date.today().isoformat()}.json")


def _codes(url: str) -> list[str]:
    r = requests.get(url, headers=_HEADERS, timeout=15)
    r.encoding = "euc-kr"
    return list(dict.fromkeys(_CODE.findall(r.text)))


def fetch(marcap_pages: int = 8, use_cache: bool = True) -> dict[str, list[str]]:
    """{'value': 거래대금 상위, 'marcap': 시총 상위} — 당일 캐시."""
    path = _cache_path()
    if use_cache and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except Exception:
            pass

    out = {"value": [], "marcap": []}
    try:
        for sosok in (0, 1):                      # 0=코스피 1=코스닥
            out["value"] += _codes(_QUANT.format(sosok))
        for sosok in (0, 1):
            for page in range(1, marcap_pages + 1):
                out["marcap"] += _codes(_MARCAP.format(sosok, page))
    except Exception:
        pass                                       # 순위를 못 받으면 호출 쪽에서 전체로 되돌린다

    out = {k: list(dict.fromkeys(v)) for k, v in out.items()}
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

    if not picked:                                 # 순위 소스가 죽으면 전체로 되돌린다
        return rows
    return list(picked.values())
