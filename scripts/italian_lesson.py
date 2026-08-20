"""매일 09:00 KST — 이탈리아어 초보용 단어 10개 + 문장 3개."""
import json
import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import groq_client, telegram

ROOT = Path(__file__).resolve().parent.parent
HISTORY_FILE = ROOT / "data" / "italian_history.json"
MAX_HISTORY = 500  # 최근 500개만 기억 (무한 증가 방지)


def load_history() -> list[str]:
    if not HISTORY_FILE.exists():
        return []
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except Exception:
        return []


def save_history(words: list[str]) -> None:
    HISTORY_FILE.parent.mkdir(parents=True, exist_ok=True)
    trimmed = words[-MAX_HISTORY:]
    HISTORY_FILE.write_text(
        json.dumps(trimmed, ensure_ascii=False, indent=2), encoding="utf-8"
    )


SYSTEM = """당신은 이탈리아어 교사입니다. 한국인 초보 학습자를 위해 실용적인 단어와 문장을 제공합니다.
반드시 다음 JSON 포맷으로만 답하세요 (다른 텍스트 금지):

{
  "words": [
    {"it": "casa", "ko": "집", "pron": "까사"}
  ],
  "sentences": [
    {"it": "Dov'è il bagno?", "ko": "화장실이 어디에 있나요?", "pron": "도베 일 바뇨"}
  ]
}

- 위 예시는 형식만 보여준 것입니다. words 배열에 정확히 10개, sentences 배열에 정확히 3개를 채우세요
- 생략 기호(...)나 주석을 절대 쓰지 말고, 완결된 JSON만 출력하세요
- 단어는 A1~A2 수준, 일상에서 자주 쓰는 것
- 발음(pron)은 한글로 한국인이 읽기 쉽게
- 문장은 여행/일상에서 바로 쓸 수 있는 것"""


def build_user_prompt(recent: list[str]) -> str:
    excluded = ", ".join(recent[-100:]) if recent else "없음"
    return f"""오늘({datetime.now().strftime('%Y-%m-%d')}) 배울 이탈리아어를 생성해주세요.

최근에 이미 배운 단어 (제외): {excluded}

새로운 10개 단어와 3개 문장을 JSON으로."""



def format_message(data: dict) -> str:
    today = datetime.now().strftime("%Y-%m-%d (%a)")
    lines = [f"🇮🇹 <b>이탈리아어 오늘의 학습</b> · {today}", ""]
    lines.append("<b>📘 단어 10개</b>")
    for i, w in enumerate(data["words"], 1):
        lines.append(f"{i:2d}. <b>{w['it']}</b> [{w['pron']}] — {w['ko']}")
    lines.append("")
    lines.append("<b>💬 문장 3개</b>")
    for i, s in enumerate(data["sentences"], 1):
        lines.append(f"{i}. <b>{s['it']}</b>")
        lines.append(f"   [{s['pron']}]")
        lines.append(f"   → {s['ko']}")
    return "\n".join(lines)


def main() -> int:
    history = load_history()
    try:
        data = groq_client.chat_json(
            SYSTEM, build_user_prompt(history), temperature=0.8, max_tokens=2500
        )
    except Exception as e:
        print(f"[italian] 생성 실패: {e}", file=sys.stderr)
        telegram.send(f"⚠️ 이탈리아어 생성 실패: {e}")
        return 1

    msg = format_message(data)
    if not telegram.send(msg):
        return 1

    new_words = [w["it"] for w in data.get("words", [])]
    save_history(history + new_words)
    print(f"[italian] 완료, 신규 {len(new_words)}개 추가")
    return 0


if __name__ == "__main__":
    sys.exit(main())
