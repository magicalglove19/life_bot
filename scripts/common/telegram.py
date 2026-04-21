"""Telegram 메시지 전송 공용 모듈."""
import os
import sys
import time
import requests

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
MAX_LEN = 4000  # Telegram 메시지 최대 4096자, 안전마진


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
        for attempt in range(3):
            try:
                r = requests.post(url, json=payload, timeout=15)
                if r.status_code == 200:
                    break
                print(f"[telegram] HTTP {r.status_code}: {r.text[:200]}", file=sys.stderr)
            except requests.RequestException as e:
                print(f"[telegram] 요청 실패 ({attempt+1}/3): {e}", file=sys.stderr)
            time.sleep(2 ** attempt)
        else:
            ok = False
    return ok


if __name__ == "__main__":
    # 테스트용: python -m scripts.common.telegram "hello"
    msg = " ".join(sys.argv[1:]) or "✅ Life Bot 테스트 메시지"
    print("전송 결과:", send(msg))
