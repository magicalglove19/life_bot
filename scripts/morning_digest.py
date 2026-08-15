"""월~금 08:00 KST — 아침 종합 브리핑.
변곡점 스캐너 + 강남자리 스캐너 + RSI(ETF 4종)를 메시지로.
한국/미국 섹션 분리 · 미국 우선 (사용자는 미국 주식 비중 높음).
"""
import sys
import threading
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram
import inflection_scanner
import daejjang_scanner
import rsi_report
import chart_patterns
import refresh_tickers

# 각 섹션별 타임아웃 (한국/미국 각각 독립)
INFLECTION_TIMEOUT_EACH = 240   # 4분
DAEJJANG_TIMEOUT_EACH = 300     # 5분
CHART_PATTERN_TIMEOUT = 480     # 8분 — yfinance 대량 배치 호출이 멈춰도 전체 브리핑은 보내지도록 하드 타임아웃
TOP_N_PER_REPORT = 15


def _run_with_timeout(fn, timeout_sec: float):
    """fn()을 별도 스레드에서 실행하고 timeout_sec 안에 못 끝나면 TimeoutError.
    yfinance 등 외부 API 호출이 멈췄을 때 전체 브리핑 전송이 막히는 것을 방지."""
    box: dict = {}

    def target():
        try:
            box["value"] = fn()
        except Exception as e:
            box["error"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout_sec)
    if t.is_alive():
        raise TimeoutError(f"{timeout_sec:.0f}초 초과 (아직 실행 중)")
    if "error" in box:
        raise box["error"]
    return box.get("value")


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
    print(f"[morning] 스캔 대상: US {len(us_symbols)} + KR {len(kr_symbols)}")

    if not kr_symbols and not us_symbols:
        telegram.send(
            "⚠️ <b>아침 브리핑</b>\n종목 리스트 파일이 없습니다.\n"
            "먼저 refresh-tickers 워크플로우를 실행해주세요."
        )
        return 1

    today = datetime.now().strftime("%Y-%m-%d (%a)")
    sections = [f"☀️ <b>아침 브리핑</b> · {today}", ""]

    # ========== 변곡점 스캐너 (US 먼저) ==========
    sections.append("<b>━━━ 🎯 변곡점 매수 신호 ━━━</b>")

    # 미국 먼저
    if us_symbols:
        try:
            print("[morning] 변곡점 US 스캔...")
            us_infl = inflection_scanner.scan_many(us_symbols, max_time_sec=INFLECTION_TIMEOUT_EACH)
            sections.append(inflection_scanner.format_report(
                "🇺🇸 미국", us_infl, limit=TOP_N_PER_REPORT
            ))
        except Exception as e:
            print(f"[morning] 변곡점 US 실패: {e}", file=sys.stderr)
            sections.append(f"🇺🇸 미국: 실패 ({str(e)[:80]})")
        sections.append("")

    # 한국
    if kr_symbols:
        try:
            print("[morning] 변곡점 KR 스캔...")
            kr_infl = inflection_scanner.scan_many(kr_symbols, max_time_sec=INFLECTION_TIMEOUT_EACH)
            sections.append(inflection_scanner.format_report(
                "🇰🇷 한국", kr_infl, limit=TOP_N_PER_REPORT
            ))
        except Exception as e:
            print(f"[morning] 변곡점 KR 실패: {e}", file=sys.stderr)
            sections.append(f"🇰🇷 한국: 실패 ({str(e)[:80]})")
        sections.append("")

    # ========== 강남자리 스캐너 (US 먼저) ==========
    sections.append("<b>━━━ 🏯 강남자리·뜬자리 ━━━</b>")

    if us_symbols:
        try:
            print("[morning] 강남자리 US 스캔...")
            us_dj = daejjang_scanner.scan_many(us_symbols, max_time_sec=DAEJJANG_TIMEOUT_EACH)
            sections.append(daejjang_scanner.format_report(
                "🇺🇸 미국", us_dj, limit=TOP_N_PER_REPORT
            ))
        except Exception as e:
            print(f"[morning] 강남자리 US 실패: {e}", file=sys.stderr)
            sections.append(f"🇺🇸 미국: 실패 ({str(e)[:80]})")
        sections.append("")

    if kr_symbols:
        try:
            print("[morning] 강남자리 KR 스캔...")
            kr_dj = daejjang_scanner.scan_many(kr_symbols, max_time_sec=DAEJJANG_TIMEOUT_EACH)
            sections.append(daejjang_scanner.format_report(
                "🇰🇷 한국", kr_dj, limit=TOP_N_PER_REPORT
            ))
        except Exception as e:
            print(f"[morning] 강남자리 KR 실패: {e}", file=sys.stderr)
            sections.append(f"🇰🇷 한국: 실패 ({str(e)[:80]})")
        sections.append("")

    # ========== RSI ==========
    try:
        print("[morning] RSI 조회...")
        sections.append(rsi_report.build_report())
    except Exception as e:
        print(f"[morning] RSI 실패: {e}", file=sys.stderr)
        sections.append(f"<b>📊 RSI</b>\n  (실패: {str(e)[:100]})")
    sections.append("")

    # ========== 차트 패턴 (컵위드핸들/더블바텀/V라인/갭상승) ==========
    # 하드 타임아웃 적용: yfinance 대량 배치 호출이 멈춰도 나머지 섹션은 반드시 전송됨
    try:
        print("[morning] 차트 패턴 스캔...")
        sections.append(_run_with_timeout(chart_patterns.build_report, CHART_PATTERN_TIMEOUT))
    except Exception as e:
        print(f"[morning] 차트 패턴 실패: {e}", file=sys.stderr)
        sections.append(f"<b>📐 차트 패턴</b>\n  (실패: {str(e)[:100]})")

    msg = "\n".join(sections)
    if not telegram.send(msg):
        return 1
    print("[morning] 전송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
