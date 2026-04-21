"""월~금 19:00 KST — 아이 퀴즈용 초등 영어 단어 10개 + 문장.
월~목: 새 단어 10개 (주간 풀에 누적)
금: 주간 풀에서 랜덤 20개 샘플 후 풀 초기화
"""
import json
import os
import random
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import groq_client, telegram

ROOT = Path(__file__).resolve().parent.parent
WEEK_FILE = ROOT / "data" / "kid_vocab_week.json"


def load_week() -> list[dict]:
    if not WEEK_FILE.exists():
        return []
    try:
        return json.loads(WEEK_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_week(items: list[dict]) -> None:
    WEEK_FILE.parent.mkdir(parents=True, exist_ok=True)
    WEEK_FILE.write_text(
        json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8"
    )


SYSTEM = """당신은 한국 초등학생(3~6학년)을 위한 영어 교사입니다.
부모가 퀴즈를 낼 수 있는 영어 단어와 간단한 예문을 만듭니다.

반드시 다음 JSON 포맷으로만 답하세요:

{
  "items": [
    {"en": "apple", "ko": "사과", "sentence": "I eat an apple every morning.", "sentence_ko": "나는 매일 아침 사과를 먹어요."},
    ...총 10개
  ]
}

- 단어 난이도: 초등 3~6학년 (일상/학교/가족/음식/동물/감정 등)
- 예문: 5~8단어의 짧고 쉬운 문장
- 단어와 예문 모두 해석 제공"""


def build_user_prompt(excluded: list[str]) -> str:
    ex = ", ".join(excluded[-200:]) if excluded else "없음"
    return f"""오늘({datetime.now().strftime('%Y-%m-%d')}) 배울 새 영어 단어 10개를 만들어주세요.

이번 주에 이미 배운 단어 (제외 필수): {ex}

새로운 10개를 JSON으로."""


def parse_response(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else t
        if t.endswith("```"):
            t = t.rsplit("```", 1)[0]
        if t.startswith("json"):
            t = t[4:].lstrip()
    return json.loads(t)


def format_new_message(items: list[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    lines = [f"👦 <b>오늘의 초등 영어 단어 10개</b> · {today}", ""]
    for i, it in enumerate(items, 1):
        lines.append(f"{i:2d}. <b>{it['en']}</b> — {it['ko']}")
        lines.append(f"    💬 {it['sentence']}")
        lines.append(f"    → {it['sentence_ko']}")
    lines.append("")
    lines.append("🎯 저녁 식사 후 퀴즈 시간!")
    return "\n".join(lines)


def format_friday_message(picked: list[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    lines = [f"🎉 <b>금요일 주간 랜덤 20개 복습</b> · {today}", ""]
    for i, it in enumerate(picked, 1):
        lines.append(f"{i:2d}. <b>{it['en']}</b> — {it['ko']}")
        lines.append(f"    💬 {it['sentence']}")
    lines.append("")
    lines.append("👨‍👩‍👧 이번 주 단어 총복습! 다음 주 월요일에 새 단어 시작합니다.")
    return "\n".join(lines)


def is_friday() -> bool:
    # 월=0, 금=4
    return datetime.now().weekday() == 4


def main() -> int:
    week = load_week()

    if is_friday():
        if not week:
            msg = "🎉 <b>금요일</b>\n\n이번 주 저장된 단어가 없어 랜덤 복습을 건너뜁니다."
            telegram.send(msg)
            return 0
        sample_size = min(20, len(week))
        picked = random.sample(week, sample_size)
        msg = format_friday_message(picked)
        if not telegram.send(msg):
            return 1
        save_week([])  # 주간 풀 초기화
        print(f"[kid_english] 금요일: {sample_size}개 랜덤 샘플 완료, 풀 초기화")
        return 0

    # 월~목: 새 단어
    excluded = [it["en"] for it in week]
    try:
        raw = groq_client.chat(SYSTEM, build_user_prompt(excluded), temperature=0.9)
        data = parse_response(raw)
        items = data.get("items", [])
        if len(items) < 5:
            raise ValueError(f"단어 수 부족: {len(items)}")
    except Exception as e:
        print(f"[kid_english] 생성 실패: {e}", file=sys.stderr)
        telegram.send(f"⚠️ 초등 영어 생성 실패: {e}")
        return 1

    msg = format_new_message(items)
    if not telegram.send(msg):
        return 1

    save_week(week + items)
    print(f"[kid_english] 신규 {len(items)}개 추가, 주간 누적 {len(week)+len(items)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
