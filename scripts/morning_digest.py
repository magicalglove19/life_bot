"""월~금 08:00 KST — 아침 종합 브리핑.
변곡점 스캐너 + 강남자리 스캐너 + RSI(ETF 4종)를 1개 메시지로.
"""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram
import inflection_scanner
import daejjang_scanner
import rsi_report
import refresh_tickers

# 전체 분석은 오래 걸릴 수 있어 각 단계 타임아웃 분리
INFLECTION_TIMEOUT = 300  # 5분
DAEJJANG_TIMEOUT = 420    # 7분
TOP_N_PER_REPORT = 10


def load_all_symbols() -> tuple[list[str], list[str]]:
    kr = refresh_tickers.load_kr_symbols()
    us = refresh_tickers.load_us_symbols()
    if not kr:
        print("[morning] KR 시가총액 파일 없음, 빈 리스트", file=sys.stderr)
    if not us:
        print("[morning] US 시가총액 파일 없음, 빈 리스트", file=sys.stderr)
    return kr, us


def main() -> int:
    kr_symbols, us_symbols = load_all_symbols()
    all_symbols = kr_symbols + us_symbols
    print(f"[morning] 스캔 대상: KR {len(kr_symbols)} + US {len(us_symbols)} = {len(all_symbols)}")

    if not all_symbols:
        telegram.send(
            "⚠️ <b>아침 브리핑</b>\n종목 리스트 파일이 없습니다.\n"
            "먼저 refresh-tickers 워크플로우를 실행해주세요."
        )
        return 1

    today = datetime.now().strftime("%Y-%m-%d (%a)")
    sections = [f"☀️ <b>아침 브리핑</b> · {today}", ""]

    # 1. 변곡점 스캐너
    try:
        print("[morning] 변곡점 스캔 시작...")
        infl_results = inflection_scanner.scan_many(
            all_symbols, max_time_sec=INFLECTION_TIMEOUT
        )
        sections.append(inflection_scanner.format_report(
            "변곡점 매수 신호", infl_results, limit=TOP_N_PER_REPORT
        ))
    except Exception as e:
        print(f"[morning] 변곡점 실패: {e}", file=sys.stderr)
        sections.append(f"<b>🎯 변곡점 매수 신호</b>\n  (실패: {str(e)[:100]})")

    sections.append("")

    # 2. 강남자리 스캐너
    try:
        print("[morning] 강남자리 스캔 시작...")
        dj_results = daejjang_scanner.scan_many(
            all_symbols, max_time_sec=DAEJJANG_TIMEOUT
        )
        sections.append(daejjang_scanner.format_report(
            "강남자리·뜬자리", dj_results, limit=TOP_N_PER_REPORT
        ))
    except Exception as e:
        print(f"[morning] 강남자리 실패: {e}", file=sys.stderr)
        sections.append(f"<b>🏯 강남자리·뜬자리</b>\n  (실패: {str(e)[:100]})")

    sections.append("")

    # 3. RSI
    try:
        print("[morning] RSI 조회...")
        sections.append(rsi_report.build_report())
    except Exception as e:
        print(f"[morning] RSI 실패: {e}", file=sys.stderr)
        sections.append(f"<b>📊 RSI</b>\n  (실패: {str(e)[:100]})")

    msg = "\n".join(sections)
    if not telegram.send(msg):
        return 1
    print("[morning] 전송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
