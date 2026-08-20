"""Groq 진단기 — Actions에서 수동 실행해 원인을 텔레그램으로 받는다.

무엇이 문제인지(키 무효 / 할당량 초과 / 모델 전멸 / 패키지 문제) 한 번에 판별한다.
API 키 값은 절대 출력하지 않는다 (접두사와 길이만).
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram

PROBE_SYSTEM = "You are terse."
PROBE_USER = "Reply with exactly: OK"


def key_summary() -> str:
    key = os.environ.get("GROQ_API_KEY") or ""
    if not key:
        return "❌ GROQ_API_KEY 없음 (Secrets 미등록 또는 이름 오타)"
    shape = "형식 정상(gsk_)" if key.startswith("gsk_") else f"⚠️ 접두사 이상: {key[:4]}..."
    return f"✅ 키 존재 · 길이 {len(key)} · {shape}"


def classify(err: str) -> str:
    e = err.lower()
    if "invalid_api_key" in e or "401" in e or "unauthorized" in e:
        return "🔑 키가 무효/폐기됨 → console.groq.com 에서 재발급 후 Secrets 갱신 필요"
    if "rate limit" in e or "429" in e or "quota" in e or "insufficient" in e:
        return "⏳ 요청 한도/할당량 초과 → 시간이 지나면 자동 회복 (또는 티어 확인)"
    if "model" in e and ("not found" in e or "decommission" in e or "does not exist" in e):
        return "📦 모델 전부 사용 불가 → 후보 목록 갱신 필요"
    if "connection" in e or "timeout" in e:
        return "🌐 네트워크/Groq 장애 → 재시도 필요"
    return "❓ 분류 불가 — 아래 원문 확인"


def main() -> int:
    lines = ["🩺 <b>Groq 진단 결과</b>", "", f"<b>1) API 키</b>", key_summary(), ""]

    try:
        from common import groq_client
    except Exception as e:
        lines += [f"<b>2) 패키지</b>", f"❌ import 실패: {e}"]
        telegram.send("\n".join(lines))
        return 1

    # 2) 모델 목록 조회 — 키가 유효한지 가장 싸게 확인되는 지점
    lines.append("<b>2) 사용 가능한 모델</b>")
    models = []
    try:
        models = groq_client.available_models()
        if models:
            lines.append(f"✅ {len(models)}개 조회됨")
            lines += [f"  · {m}" for m in models[:15]]
        else:
            lines.append("⚠️ 목록이 비어있음 (키 권한 또는 조회 실패)")
    except Exception as e:
        lines.append(f"❌ 조회 실패: {e}")
        lines.append(classify(str(e)))

    # 3) 실제 호출 — 후보 순회 + 자동 탐색까지 그대로 태워본다
    lines += ["", "<b>3) 실제 호출 테스트</b>"]
    try:
        out = groq_client.chat(PROBE_SYSTEM, PROBE_USER, max_tokens=20)
        lines.append(f"✅ 성공 — 응답: {out[:80]}")
        lines += ["", "결론: Groq는 정상입니다. 이탈리아어/영어가 계속 안 오면 다른 원인입니다."]
        verdict = 0
    except Exception as e:
        lines.append("❌ 실패")
        lines.append(classify(str(e)))
        lines.append("")
        lines.append(f"<b>원문</b>\n<code>{str(e)[:600]}</code>")
        verdict = 1

    msg = "\n".join(lines)
    print(msg)
    telegram.send(msg)
    return verdict


if __name__ == "__main__":
    sys.exit(main())
