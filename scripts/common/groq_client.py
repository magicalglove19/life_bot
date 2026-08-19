"""Groq LLM 호출 공용 모듈 (무료 티어용).

Groq는 구형 모델을 수시로 정리(decommission)하기 때문에, 하드코딩해 둔 모델명이
어느 날 갑자기 404가 되면서 매일 돌던 작업이 통째로 실패한다.
그래서 (1) 후보 모델을 순서대로 시도하고, (2) 후보가 전부 죽었으면
/models 에서 실제 사용 가능한 모델을 받아와 재시도한다.
"""
import os
import sys
import time

try:
    from groq import Groq
except ImportError:
    Groq = None

# 앞에서부터 순서대로 시도. 앞쪽이 품질 우선, 뒤쪽이 가볍고 한도 여유 있는 모델.
MODEL_CANDIDATES = [
    "llama-3.3-70b-versatile",
    "openai/gpt-oss-120b",
    "moonshotai/kimi-k2-instruct",
    "qwen/qwen3-32b",
    "llama-3.1-8b-instant",
    "openai/gpt-oss-20b",
]

DEFAULT_MODEL = MODEL_CANDIDATES[0]
FALLBACK_MODEL = "llama-3.1-8b-instant"

# 자동 탐색 시 제외할 비(非)채팅 모델
_NON_CHAT_HINTS = ("whisper", "tts", "guard", "embed", "compound", "vision")

# 모델 자체가 없어졌을 때 나오는 신호 (재시도해도 소용없음 → 즉시 다음 모델)
_DEAD_MODEL_HINTS = (
    "model_not_found",
    "decommissioned",
    "does not exist",
    "has been deprecated",
    "404",
)

_discovered: list[str] | None = None


def _client():
    if Groq is None:
        raise RuntimeError("groq 패키지가 설치되지 않음. pip install groq")
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY 환경변수 누락")
    return Groq(api_key=api_key)


def _is_dead_model(err: Exception) -> bool:
    msg = str(err).lower()
    return any(h in msg for h in _DEAD_MODEL_HINTS)


def available_models(client=None) -> list[str]:
    """계정에서 실제로 쓸 수 있는 채팅 모델 id 목록."""
    global _discovered
    if _discovered is not None:
        return _discovered

    client = client or _client()
    ids = []
    try:
        for m in client.models.list().data:
            mid = getattr(m, "id", "") or ""
            if not mid or any(h in mid.lower() for h in _NON_CHAT_HINTS):
                continue
            ids.append(mid)
    except Exception as e:
        print(f"[groq] 모델 목록 조회 실패: {e}", file=sys.stderr)

    _discovered = ids
    return ids


def chat(
    system: str,
    user: str,
    model: str | None = None,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    retries: int = 3,
) -> str:
    """Groq chat 호출. 모델이 죽었으면 다음 후보로, 후보가 다 죽었으면 자동 탐색."""
    client = _client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    # 시도 순서: 지정 모델 → 후보 목록 (중복 제거)
    order: list[str] = []
    for m in ([model] if model else []) + MODEL_CANDIDATES:
        if m and m not in order:
            order.append(m)

    tried: list[str] = []
    last_err: Exception | None = None
    discovery_done = False

    while order:
        m = order.pop(0)
        tried.append(m)
        for attempt in range(retries):
            try:
                resp = client.chat.completions.create(
                    model=m,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                if m != DEFAULT_MODEL:
                    print(f"[groq] {m} 사용 (기본 모델 대체)", file=sys.stderr)
                return resp.choices[0].message.content.strip()
            except Exception as e:
                last_err = e
                if _is_dead_model(e):
                    # 모델이 사라진 것 → 재시도 의미 없음, 바로 다음 후보로
                    print(f"[groq] {m} 사용 불가(정리된 모델): {e}", file=sys.stderr)
                    break
                print(f"[groq] {m} 실패 ({attempt+1}/{retries}): {e}", file=sys.stderr)
                time.sleep(2 ** attempt)

        # 후보를 다 써버렸으면 계정에서 실제 가능한 모델을 받아와 한 번 더 기회를 준다
        if not order and not discovery_done:
            discovery_done = True
            live = [x for x in available_models(client) if x not in tried]
            if live:
                print(f"[groq] 후보 소진 → 자동 탐색 모델 시도: {live[:3]}", file=sys.stderr)
                order = live[:3]

    raise RuntimeError(
        f"Groq 호출 최종 실패 (시도한 모델: {', '.join(tried)}) — 마지막 오류: {last_err}"
    )


if __name__ == "__main__":
    # 진단용: python3 scripts/common/groq_client.py
    try:
        models = available_models()
        print(f"사용 가능한 채팅 모델 {len(models)}개:")
        for m in models:
            print(f"  - {m}")
        print("\n테스트 호출...")
        print(chat("You are helpful.", "Say hello in Korean and Italian."))
    except Exception as e:
        print(f"실패: {e}", file=sys.stderr)
        sys.exit(1)
