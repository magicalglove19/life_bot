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


SYSTEM = """당신은 한국 중학생(중1~중2)을 위한 **실용 영어** 교사입니다.
실제 비행기 여행, 학교생활, 친구·식당·길찾기 등 현장에서 바로 쓰는 영어 단어와 회화 문장을 만듭니다.

반드시 다음 JSON 포맷으로만 답하세요 (다른 텍스트 금지):

{
  "items": [
    {"en": "boarding pass", "ko": "탑승권", "type": "dialog",
     "example": "A: May I see your boarding pass, please? B: Sure, here you go.",
     "example_ko": "A: 탑승권 좀 보여주시겠어요? B: 네, 여기 있어요."},
    {"en": "raise your hand", "ko": "손을 들다", "type": "sentence",
     "example": "If you have a question, please raise your hand before you speak.",
     "example_ko": "질문이 있으면 말하기 전에 손을 들어 주세요."}
  ]
}

규칙:
- 위 예시는 형식만 보여준 것입니다. items 배열에 정확히 10개를 채우세요
- 생략 기호(...)나 주석을 절대 쓰지 말고, 완결된 JSON만 출력하세요
- 난이도: **중1~중2 / CEFR A2후반~B1** 수준의 **실용 회화 영어**
- **구성 비율 (10개 중)**:
  - 8개: 아래 5개 주제에서 다양하게 섞은 **실용 회화 표현**
  - 2개: 중학생 교과서 수준의 **추상·감정 어휘** (encourage, opportunity, recognize, environment, achievement, embarrassed, exhausted, recommend, confident, situation, decision, mention 등에서 선택)
- 실용 회화 8개의 주제 풀 (한 주제만 몰빵 금지):
  1) **비행기·공항 여행** — check-in, boarding pass, aisle seat, carry-on, layover, customs, baggage claim, fasten your seatbelt, turbulence, gate
  2) **학교생활** — raise your hand, take notes, group project, due date, school cafeteria, locker, principal, field trip, P.E. class, hand in homework
  3) **친구·일상 회화** — hang out, give me a ride, get along with, on my way, made it, no big deal, sounds good
  4) **식당·쇼핑·길찾기** — for here or to go, refill, fitting room, on sale, go straight, turn right, around the corner
  5) **호텔·여행지** — check in/out, front desk, room service, complimentary breakfast, sightseeing
- 실용 표현은 단일 단어가 아니어도 OK — 구동사·콜로케이션 우선 ("give me a ride", "on my way", "hand in", "check in")
- **금지**: apple, dog, cat, book, happy, family, food, smile, friend, pencil 등 초급 단어. 토플·수능 고급(sophisticated, ambiguous 등) 출제 금지
- **문장 10개 중 정확히 5개는 "sentence", 5개는 "dialog"** 로 구성
  - sentence: 8~14단어, 실제 상황에서 쓸 법한 자연스러운 문장
  - dialog: "A: ... B: ..." 형태, 전체 14~22단어, **공항·교실·식당 등 구체 상황**
- 모든 예문은 한국 중학생이 외국에서 또는 영어 수업·여행 중 **그대로 따라 말할 수 있는 라이브 회화**
- "I am ...", "I have ...", "I like ..." 같은 단순 문형만 반복 금지
- 단어·예문 모두 자연스러운 한국어 해석 제공"""


def build_user_prompt(excluded: list[str]) -> str:
    ex = ", ".join(excluded[-200:]) if excluded else "없음"
    return f"""오늘({datetime.now().strftime('%Y-%m-%d')}) 배울 새 영어 단어 10개를 만들어주세요.

이번 주에 이미 배운 단어 (제외 필수): {ex}

새로운 10개를 JSON으로."""



def _example_text(it: dict) -> str:
    """신규 포맷(example/example_ko) + 구버전 포맷(sentence/sentence_ko) 모두 지원."""
    ex = it.get("example") or it.get("sentence") or ""
    ex_ko = it.get("example_ko") or it.get("sentence_ko") or ""
    return ex, ex_ko


def _icon(it: dict) -> str:
    t = (it.get("type") or "").lower()
    if t == "dialog":
        return "🗨️"
    return "💬"


def format_new_message(items: list[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    lines = [f"👦 <b>오늘의 영어 단어 10개</b> (중1~중2 / B1) · {today}", ""]
    for i, it in enumerate(items, 1):
        ex, ex_ko = _example_text(it)
        lines.append(f"{i:2d}. <b>{it['en']}</b> — {it['ko']}")
        lines.append(f"    {_icon(it)} {ex}")
        if ex_ko:
            lines.append(f"    → {ex_ko}")
    lines.append("")
    lines.append("🎯 저녁 식사 후 퀴즈 시간! (🗨️ = 대화, 💬 = 문장)")
    return "\n".join(lines)


def format_friday_message(picked: list[dict]) -> str:
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    lines = [f"🎉 <b>금요일 주간 랜덤 20개 복습</b> · {today}", ""]
    for i, it in enumerate(picked, 1):
        ex, _ = _example_text(it)
        lines.append(f"{i:2d}. <b>{it['en']}</b> — {it['ko']}")
        if ex:
            lines.append(f"    {_icon(it)} {ex}")
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
        data = groq_client.chat_json(
            SYSTEM, build_user_prompt(excluded), temperature=0.9, max_tokens=3500
        )
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
