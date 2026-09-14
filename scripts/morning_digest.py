# -*- coding: utf-8 -*-
"""월~금 08:00 KST — 아침 종합 브리핑.

■ 2026-09 전면 개편
예전에는 변곡점·강남자리·차트패턴 결과를 섹션별로 나열해 하루 60~75종목이 왔고,
점수가 없어 무엇부터 볼지 알 수 없었다. 백테스트(미국 395종목 × 3개 시장구간)
결과 패턴 신호 자체엔 엣지가 없었고(무작위 매수 대비 +0.2%p), 실제로 결과를
가른 건 '6개월 상대강도'와 'ATR 변동성'이었다. 자세한 근거는 signal_rank.py.

새 구조:
  1) market_data.fetch()로 시장별 시세를 한 번에 받는다 (종목별 호출 → 타임아웃 해소)
  2) 변곡점 2종 + 차트패턴 3종을 같은 데이터로 탐지
  3) signal_rank의 관문(RS·ATR·거래량·120MA·거래대금)을 통과한 종목만 발송
  4) 강남자리 점수는 참고 태그로 붙인다 (백테스트에 포함되지 않아 관문에서 제외)
  5) 한국은 발송하지 않는다. 코스피 296종목으로 따로 검증한 결과 세 구간 모두
     플러스인 조합은 ATR≥6%+거래량≥1.5x뿐이었고 하락장 기여가 +0.24%p로 0에 가까웠다.
     RS·120MA 관문은 한국 하락장에서 오히려 해로웠다(-3.3~-6.4%p). 즉 검증된 엣지가
     없는데 종목만 올라와서 매수를 유도한다. 한국용 관문 상수는 signal_rank.py에
     남겨뒀으니 다시 켜려면 그쪽을 참고할 것.
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram, timeutil
import market_data
import inflection_scanner
import chart_patterns
import daejjang_scanner
import signal_rank
import rsi_report
import refresh_tickers

US_BENCH = "SPY"
MARKET_TIMEOUT = 600    # 다운로드 + 스캔
TOP_N = 15             # 출근 준비하며 훑어볼 수 있는 상한


def _run_with_timeout(fn, timeout_sec: float):
    """fn()을 별도 스레드에서 실행. 외부 API가 멈춰도 브리핑 전송은 막지 않는다."""
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


def merge_patterns(store: dict) -> dict[str, list[str]]:
    """변곡점 2종 + 차트패턴 3종을 종목별로 합친다."""
    merged: dict[str, list[str]] = {}

    for sym, df in store.items():
        try:
            pats = inflection_scanner.detect(df, signal_rank.LOOKBACK_BARS)
        except Exception as e:
            print(f"[morning] 변곡점 {sym} 실패: {e}", file=sys.stderr)
            continue
        if pats:
            merged.setdefault(sym, []).extend(pats)

    for sym, pats in chart_patterns.detect_store(store, signal_rank.LOOKBACK_BARS).items():
        for p in pats:
            if p not in merged.setdefault(sym, []):
                merged[sym].append(p)

    return merged


def daejjang_tags(store: dict, symbols: list[str]) -> dict[str, str]:
    """통과 종목에 한해 강남자리 판정을 붙인다 (네트워크 호출 없음)."""
    tags = {}
    for sym in symbols:
        try:
            r = daejjang_scanner.analyze_df(sym, store.get(sym))
        except Exception:
            continue
        if r and r["recent_rec"] in ("BUY", "WATCH"):
            tags[sym] = f"🏯강남자리 {r['recent_rec']} {r['recent_score']}점"
    return tags


def scan_market(symbols: list[str], bench_symbol: str, is_kr: bool) -> tuple[dict, dict]:
    """한 시장 전체 스캔. (rank 결과, 강남자리 태그) 반환."""
    store = market_data.fetch(symbols + [bench_symbol])
    bench_df = store.pop(bench_symbol, None)
    if bench_df is None or len(bench_df) < signal_rank.RS_PERIOD + 1:
        raise RuntimeError(f"벤치마크 {bench_symbol} 데이터 없음")
    bench = bench_df["Close"]

    patterns = merge_patterns(store)
    print(f"[morning] 패턴 발생 {len(patterns)}종목 / 수신 {len(store)}종목")

    result = signal_rank.rank(store, bench, patterns, is_kr=is_kr,
                              names=refresh_tickers.load_names())
    tags = daejjang_tags(store, [m["symbol"] for m in signal_rank.top_n(result, TOP_N)])
    return result, tags


def render(title: str, result: dict, tags: dict, is_kr: bool) -> str:
    return signal_rank.format_report(title, result, is_kr=is_kr, limit=TOP_N, tags=tags)


def main() -> int:
    us_symbols = refresh_tickers.load_us_symbols()
    print(f"[morning] 스캔 대상: US {len(us_symbols)}종목")

    if not us_symbols:
        telegram.send(
            "⚠️ <b>아침 브리핑</b>\n미국 종목 리스트 파일이 없습니다.\n"
            "먼저 refresh-tickers 워크플로우를 실행해주세요."
        )
        return 1

    sections = [
        f"☀️ <b>아침 브리핑</b> · {timeutil.stamp()} {timeutil.stamp('%H:%M')} KST",
        f"<i>🇺🇸 미국 · {signal_rank.MIN_SCORE}점 이상 최대 {TOP_N}개 · "
        f"{signal_rank.HOLD_DAYS}거래일 보유 기준</i>",
        "",
    ]

    if us_symbols:
        try:
            print("[morning] 미국 스캔...")
            result, tags = _run_with_timeout(
                lambda: scan_market(us_symbols, US_BENCH, is_kr=False), MARKET_TIMEOUT)
            sections.append(render("━━━ 🎯 매수 후보 ━━━", result, tags, False))
        except Exception as e:
            print(f"[morning] 미국 실패: {e}", file=sys.stderr)
            sections.append(f"━━━ 🎯 매수 후보 ━━━\n  실패 ({str(e)[:80]})")
        sections.append("")

    try:
        sections.append(rsi_report.build_report())
    except Exception as e:
        print(f"[morning] RSI 실패: {e}", file=sys.stderr)
        sections.append(f"<b>📊 RSI</b>\n  (실패: {str(e)[:100]})")

    sections += [
        "",
        "<i>점수 = RS40 + ATR25 + 거래량15 + 120MA10 + 거래대금5 + 패턴5. "
        f"{signal_rank.MIN_SCORE}점 이상: 2022 하락장 SPY 대비 +1.0%p(유의X) / 2023 +3.2%p. "
        f"{signal_rank.HOLD_DAYS}거래일 보유·20일 내 평균 -11% 낙폭 감내 비중 전제. 매수 추천 아님.</i>",
    ]

    if "--dry-run" in sys.argv:
        print("\n".join(sections))
        return 0
    if not telegram.send("\n".join(sections)):
        return 1
    print("[morning] 전송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
