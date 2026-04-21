"""Groq LLM 호출 공용 모듈 (무료 티어용)."""
import os
import sys
import time

try:
    from groq import Groq
except ImportError:
    Groq = None

DEFAULT_MODEL = "llama-3.3-70b-versatile"
FALLBACK_MODEL = "llama-3.1-8b-instant"


def _client():
    if Groq is None:
        raise RuntimeError("groq 패키지가 설치되지 않음. pip install groq")
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY 환경변수 누락")
    return Groq(api_key=api_key)


def chat(
    system: str,
    user: str,
    model: str = DEFAULT_MODEL,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    retries: int = 3,
) -> str:
    """Groq chat 호출. 실패 시 fallback 모델 재시도."""
    client = _client()
    messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]

    attempted_models = [model]
    if model != FALLBACK_MODEL:
        attempted_models.append(FALLBACK_MODEL)

    last_err = None
    for m in attempted_models:
        for attempt in range(retries):
            try:
                resp = client.chat.completions.create(
                    model=m,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                return resp.choices[0].message.content.strip()
            except Exception as e:
                last_err = e
                print(f"[groq] {m} 실패 ({attempt+1}/{retries}): {e}", file=sys.stderr)
                time.sleep(2 ** attempt)
    raise RuntimeError(f"Groq 호출 최종 실패: {last_err}")


if __name__ == "__main__":
    print(chat("You are helpful.", "Say hello in Korean, Italian, and English."))
