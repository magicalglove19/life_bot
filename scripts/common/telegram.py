"""Telegram 메시지 전송 공용 모듈."""
import os
import sys
import time
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4000          # Telegram 메시지 최대 4096자, 안전마진
REQUEST_TIMEOUT = 20    # 초
MAX_ATTEMPTS = 3        # 429(확실한 거절)일 때만 소진된다
RETRY_AFTER_DEFAULT = 3
RETRY_AFTER_MAX = 30


def _split(text: str, max_len: int = MAX_LEN):
    """긴 메시지를 줄 단위로 쪼갬."""
    if len(text) <= max_len:
        return [text]
    parts, buf = [], ""
    for line in text.split("\n"):
        if len(buf) + len(line) + 1 > max_len:
            parts.append(buf)
            buf = line
        else:
            buf = f"{buf}\n{line}" if buf else line
    if buf:
        parts.append(buf)
    return parts


def send(text: str, parse_mode: str = "HTML") -> bool:
    """텔레그램으로 메시지 전송. 성공 시 True."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        print("[telegram] TELEGRAM_BOT_TOKEN/CHAT_ID 환경변수 누락", file=sys.stderr)
        return False

    url = TELEGRAM_API.format(token=token)
    ok = True
    for chunk in _split(text):
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": parse_mode,
            "disable_web_page_preview": True,
        }
        if not _send_chunk(url, payload):
            ok = False
    return ok


def _send_chunk(url: str, payload: dict) -> bool:
    """조각 하나 전송. 중복 발송 방지가 최우선.

    텔레그램은 메시지를 실제로 전달하고도 5xx나 타임아웃을 돌려주는 경우가 있다.
    그때 재전송하면 같은 내용이 두 번, 세 번 간다. 그래서 '확실히 전달되지 않은'
    429(요청 한도)만 재시도하고, 전달 여부가 불확실한 응답에서는 재전송하지 않는다.
    """
    for attempt in range(MAX_ATTEMPTS):
        try:
            r = requests.post(url, json=payload, timeout=REQUEST_TIMEOUT)
        except requests.RequestException as e:
            # 전달됐는지 알 수 없음 → 재전송하면 중복 위험. 여기서 멈춘다.
            print(f"[telegram] 네트워크 오류(전달 여부 불명, 재전송 안 함): {e}", file=sys.stderr)
            return False

        if r.status_code == 200:
            return True

        if r.status_code == 429:
            # 한도 초과는 '거절'이 확실하므로 재시도해도 중복되지 않는다
            wait = RETRY_AFTER_DEFAULT
            try:
                wait = int(r.json().get("parameters", {}).get("retry_after", wait))
            except Exception:
                pass
            wait = min(wait, RETRY_AFTER_MAX)
            print(f"[telegram] 429 한도 초과 — {wait}초 후 재시도 ({attempt+1}/{MAX_ATTEMPTS})", file=sys.stderr)
            time.sleep(wait)
            continue

        # 4xx: 요청 자체가 잘못됨(파싱 오류 등) → 재시도해도 같은 결과
        # 5xx: 전달됐을 수 있음 → 재전송하면 중복
        print(f"[telegram] HTTP {r.status_code} (재전송 안 함): {r.text[:200]}", file=sys.stderr)
        return False

    return False


if __name__ == "__main__":
    # 테스트용: python -m scripts.common.telegram "hello"
    msg = " ".join(sys.argv[1:]) or "✅ Life Bot 테스트 메시지"
    print("전송 결과:", send(msg))
