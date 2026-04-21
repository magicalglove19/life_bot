"""월~금 18:00 KST — Nasdaq 공식 페이지에서 Pre-market 급등주 5개.

NASDAQ는 public JSON API(api.nasdaq.com)를 제공.
premarket 섹션: /api/marketmovers/PRE
"""
import sys
from datetime import datetime
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram

NUM_MOVERS = 5
URLS = [
    # Nasdaq 공식 API — pre-market gainers
    "https://api.nasdaq.com/api/market-info",  # 상태 확인용
    "https://api.nasdaq.com/api/marketmovers/PRE",
]

# 폴백: HTML 페이지 스크래핑
FALLBACK_URL = "https://www.nasdaq.com/market-activity/pre-market"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/121.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://www.nasdaq.com",
    "Referer": "https://www.nasdaq.com/",
}


def fetch_api() -> list[dict]:
    """Nasdaq 공식 API에서 pre-market 급등주 가져오기."""
    r = requests.get(
        "https://api.nasdaq.com/api/marketmovers/PRE",
        headers=HEADERS,
        timeout=20,
    )
    r.raise_for_status()
    payload = r.json()
    data = payload.get("data") or {}
    # 가능한 경로 탐색 (API 구조가 때때로 변함)
    gainers = (
        data.get("gainers", {}).get("rows")
        or data.get("gainers")
        or []
    )
    if isinstance(gainers, dict):
        gainers = gainers.get("rows", [])
    return gainers or []


def parse_item(row: dict) -> dict | None:
    """API 응답 row → 표준 포맷."""
    sym = (row.get("symbol") or "").strip()
    if not sym:
        return None
    name = row.get("companyName") or row.get("name") or ""
    last = row.get("lastSalePrice") or row.get("price") or ""
    change = row.get("netChange") or row.get("change") or ""
    change_pct = row.get("percentageChange") or row.get("pctChange") or ""
    volume = row.get("volume") or ""
    return {
        "symbol": sym,
        "name": str(name)[:40],
        "price": str(last),
        "change": str(change),
        "change_pct": str(change_pct),
        "volume": str(volume),
    }


def format_message(items: list[dict]) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M KST")
    lines = [f"🚀 <b>미국 Pre-market 급등주 TOP {len(items)}</b>", f"<i>{ts}</i>", ""]
    for i, it in enumerate(items, 1):
        lines.append(
            f"<b>{i}. {it['symbol']}</b> {it['change_pct']} · {it['price']}\n"
            f"   {it['name']}"
        )
    lines.append("")
    lines.append("<i>출처: Nasdaq Pre-Market Activity</i>")
    return "\n".join(lines)


def main() -> int:
    try:
        rows = fetch_api()
    except Exception as e:
        print(f"[premarket] API 실패: {e}", file=sys.stderr)
        telegram.send(f"⚠️ Pre-market API 호출 실패: {e}")
        return 1

    items = []
    for row in rows:
        parsed = parse_item(row)
        if parsed:
            items.append(parsed)
        if len(items) >= NUM_MOVERS:
            break

    if not items:
        telegram.send("⚠️ Pre-market 데이터가 비어있습니다 (장 휴장이거나 공급 지연).")
        return 0

    msg = format_message(items)
    if not telegram.send(msg):
        return 1
    print(f"[premarket] 완료, {len(items)}개 전송")
    return 0


if __name__ == "__main__":
    sys.exit(main())
