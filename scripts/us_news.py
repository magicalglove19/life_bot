"""매일 14:00 KST — MarketWatch RSS에서 미국 경제 뉴스 3개 + 한국어 번역."""
import sys
from datetime import datetime
from pathlib import Path

import feedparser

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import groq_client, telegram, timeutil

# MarketWatch RSS 피드 후보 (Top Stories / Real-Time Headlines)
FEEDS = [
    "https://feeds.content.dowjones.io/public/rss/mw_topstories",
    "https://feeds.content.dowjones.io/public/rss/mw_realtimeheadlines",
    "https://feeds.content.dowjones.io/public/rss/mw_marketpulse",
]

NUM_NEWS = 3
SYSTEM = """당신은 영어-한국어 금융/경제 번역가입니다.
사용자가 준 영문 뉴스 요약을 자연스러운 한국어로 번역합니다.
- 용어는 금융권에서 통용되는 한국어 표기 사용
- 원문의 핵심만 간결하게 (2~3문장)
- 번역문만 출력, 다른 설명 금지"""


def fetch_news(n: int = NUM_NEWS) -> list[dict]:
    """여러 피드에서 최신 뉴스를 모아 중복 제거 후 상위 N개."""
    seen_titles = set()
    collected = []
    for url in FEEDS:
        parsed = feedparser.parse(url)
        for entry in parsed.entries:
            title = (entry.get("title") or "").strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            collected.append({
                "title": title,
                "link": entry.get("link", ""),
                "summary": (entry.get("summary") or "").strip(),
                "published": entry.get("published", ""),
            })
        if len(collected) >= n * 3:
            break
    return collected[:n]


def translate(text: str) -> str:
    if not text:
        return ""
    try:
        return groq_client.chat(SYSTEM, text, temperature=0.3, max_tokens=500)
    except Exception as e:
        print(f"[us_news] 번역 실패: {e}", file=sys.stderr)
        return "(번역 실패)"


def clean_html(html: str) -> str:
    """RSS summary에서 간단한 HTML 태그 제거."""
    from html import unescape
    import re
    t = re.sub(r"<[^>]+>", " ", html)
    t = re.sub(r"\s+", " ", t).strip()
    return unescape(t)


def format_message(items: list[dict]) -> str:
    today = timeutil.stamp()
    lines = [f"📰 <b>미국 경제 뉴스 TOP {len(items)}</b> · {today}", ""]
    for i, it in enumerate(items, 1):
        summary_en = clean_html(it["summary"])[:400]
        summary_ko = translate(f"제목: {it['title']}\n요약: {summary_en}") if summary_en else translate(it["title"])
        lines.append(f"<b>{i}. {it['title']}</b>")
        if it.get("link"):
            lines.append(f"🔗 {it['link']}")
        lines.append(f"🇰🇷 {summary_ko}")
        lines.append("")
    return "\n".join(lines).rstrip()


def main() -> int:
    news = fetch_news()
    if not news:
        telegram.send("⚠️ 뉴스 피드를 가져오지 못했습니다.")
        return 1
    msg = format_message(news)
    if not telegram.send(msg):
        return 1
    print(f"[us_news] 완료, {len(news)}개 전송")
    return 0


if __name__ == "__main__":
    sys.exit(main())
