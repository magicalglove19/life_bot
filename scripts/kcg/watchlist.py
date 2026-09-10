"""관심종목 자동 등록/추적.

오늘 상한가·신고가가 나온 종목을 파일에 쌓아두고, 다음 실행부터
"트리거 이후 며칠째"를 이어서 추적한다. 트리거 후 max_age 거래일이
지나면 자동으로 만료시킨다.
"""

from __future__ import annotations

import datetime as dt
import json
import os

PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "watchlist.json")


def load(path: str = PATH) -> dict[str, dict]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return {}


def save(items: dict[str, dict], path: str = PATH) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(items, fh, ensure_ascii=False, indent=1)


def update(items: dict[str, dict], candidates: list, expire_days: int = 30) -> tuple[dict, list]:
    """오늘 트리거가 난 종목을 등록하고, 오래된 항목은 버린다.

    (갱신된 목록, 새로 등록된 종목 리스트) 를 반환한다.
    """
    today = dt.date.today().isoformat()
    fresh = []
    for c in candidates:
        t = c.today_trigger
        if t is None:
            continue
        key = c.code
        if key in items and items[key].get("trigger_date") == str(t.date.date()):
            continue
        items[key] = {
            "name": c.name,
            "market": c.market,
            "kind": t.label,
            "trigger_date": str(t.date.date()),
            "trigger_close": round(t.close),
            "registered": today,
        }
        fresh.append(c)

    cutoff = (dt.date.today() - dt.timedelta(days=expire_days)).isoformat()
    items = {k: v for k, v in items.items() if v.get("trigger_date", "") >= cutoff}
    return items, fresh
