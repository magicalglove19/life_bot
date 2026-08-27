"""월~토 05:30 KST — 마크 미너비니 SEPA 스크리너 (미국주식 S&P 500).

아침 브리핑(morning-stocks, 05:00 KST)과 같은 시간대에 도착한다.
미국장 마감 직후라 당일 종가가 확정된 상태로 판정한다.

메시지가 길어 잘리는 것을 막기 위해 항상 2편으로 나눠 보낸다.
  1편 — 시장 국면 + 지금 실행 가능한 자리 (돌파 / 매수구간)
  2편 — 신규 셋업 · 초타이트 엄선 · Stage 2 요약

판정 로직은 원본(2026 마크미니 스크리너/minervini)을 그대로 벤더링한 것이며,
여기서는 결과를 텔레그램용으로 요약만 한다.
"""
import os
import sys
import datetime as dt
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram
from minervini import screener
from minervini.config import Config

KST = dt.timezone(dt.timedelta(hours=9))

# 계좌 설정 — 수량 계산에만 쓰인다. 워크플로 env로 덮어쓸 수 있다.
ACCOUNT = float(os.environ.get("MINERVINI_ACCOUNT", "100000"))
RISK_PCT = float(os.environ.get("MINERVINI_RISK", "1.25"))
MIN_SCORE = float(os.environ.get("MINERVINI_MIN_SCORE", "70"))

MAX_PART_CHARS = 3400   # 텔레그램 4096자 한도에 여유를 둔 편당 상한
SECTION_LIMIT = 8       # 섹션당 최대 종목 수
TIGHT_LAST_DEPTH = 8.0  # 초타이트 판정: 마지막 수축 %

LIGHT_EMOJI = {"초록불": "🟢", "노란불": "🟡", "주황불": "🟠", "빨간불": "🔴", "회색불": "⚪"}


def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def num(v, digits=2, dash="-") -> str:
    try:
        if not np.isfinite(v):
            return dash
        return f"{v:,.{digits}f}"
    except Exception:
        return dash


def pivot_age(c) -> str:
    if c.days_past_pivot < 0:
        return "피벗 대기"
    if c.days_past_pivot == 0:
        return "오늘 돌파"
    return f"돌파 {c.days_past_pivot}일째"


def stock_line(c, with_exec: bool = True) -> str:
    """종목 한 건. 2줄 — 첫 줄은 정체, 둘째 줄은 실행 정보."""
    head = (f"• <b>{esc(c.ticker)}</b> ${num(c.price)} · RS {num(c.rs_rating, 0)}"
            f" · 점수 {num(c.total_score, 0)}")
    if not with_exec:
        age = f" · 셋업 {c.setup_days}일째" if c.setup_days else ""
        return head + age

    stop_txt = f"{num(c.stop)} ({num(-c.stop_pct, 1)}%)" if np.isfinite(c.stop_pct) else num(c.stop)
    detail = f"   진입 {num(c.entry)} / 손절 {stop_txt}"
    if c.shares:
        detail += f" · 수량 {c.shares}"
    detail += f" · {pivot_age(c)}"
    return f"{head}\n{detail}"


def section(title: str, items: list, with_exec: bool = True, empty: str = "해당 없음") -> str:
    lines = [f"<b>{title}</b> ({len(items)})" if items else f"<b>{title}</b>"]
    if not items:
        lines.append(empty)
        return "\n".join(lines)
    for c in items[:SECTION_LIMIT]:
        lines.append(stock_line(c, with_exec))
    if len(items) > SECTION_LIMIT:
        lines.append(f"   … 외 {len(items) - SECTION_LIMIT}건")
    return "\n".join(lines)


def market_block(r) -> str:
    emoji = LIGHT_EMOJI.get(r.light, "⚪")
    lines = [f"📊 <b>시장 국면</b> — {emoji} {esc(r.light)}",
             f"권장 노출도: <b>{esc(r.exposure)}</b>"]
    if np.isfinite(r.price):
        pos = []
        pos.append("50일선 " + ("위" if r.above_ma50 else "아래"))
        pos.append("200일선 " + ("위" if r.above_ma200 else "아래"))
        pos.append("200일선 " + ("우상향" if r.ma200_up else "하락"))
        lines.append(f"{esc(r.symbol)} {num(r.price)} · " + " · ".join(pos))
        lines.append(f"52주 고점 대비 {num(r.pct_from_high, 1)}% · "
                     f"Stage 2 비율 {num(r.breadth_stage2, 0)}% · "
                     f"200일선 위 {num(r.breadth_above_ma200, 0)}%")
    if r.comment:
        lines.append(f"<i>{esc(r.comment)}</i>")
    return "\n".join(lines)


def trim(parts: list[str]) -> str:
    """편 하나가 상한을 넘지 않도록 뒤쪽 섹션부터 잘라낸다."""
    text = "\n\n".join(parts)
    while len(text) > MAX_PART_CHARS and len(parts) > 2:
        parts = parts[:-1]
        text = "\n\n".join(parts + ["<i>…이하 생략 (길이 제한)</i>"])
    return text


def build_messages(res) -> list[str]:
    cands = [c for c in res.candidates if c.vcp.is_vcp and c.total_score >= MIN_SCORE]

    breakout = [c for c in cands if c.vcp.status == "돌파" and 0 <= c.days_past_pivot <= 5]
    buyzone = [c for c in cands if c.vcp.status == "매수구간"]
    fresh = [c for c in cands if c.setup_days and c.setup_days <= 3]
    tight = [c for c in cands
             if c.vcp.depths and c.vcp.depths[-1] <= TIGHT_LAST_DEPTH
             and np.isfinite(c.vcp.dryup_ratio) and c.vcp.dryup_ratio <= 0.85]

    now = dt.datetime.now(KST)
    stamp = now.strftime("%m/%d (%a) %H:%M")

    # ---------- 1편: 국면 + 지금 실행할 자리 ----------
    head1 = (f"🇺🇸 <b>미너비니 스크리너</b> · {stamp}  <b>(1/2)</b>\n"
             f"S&amp;P 500 {res.scanned}종목 스캔 · Stage 2 통과 {len(res.stage2)}"
             f" · 종합 {num(MIN_SCORE, 0)}점 이상 {len(cands)}종목")
    part1 = trim([
        head1,
        market_block(res.regime),
        section("🚀 지금 돌파 중", breakout, empty="없음 — 무리해서 쫓아가지 않는다"),
        section("🎯 매수구간 대기 (피벗 6% 이내)", buyzone, empty="없음"),
    ])

    # ---------- 2편: 관심 목록 ----------
    head2 = f"🇺🇸 <b>미너비니 스크리너</b> · {stamp}  <b>(2/2)</b>"
    stage2_line = ""
    if res.stage2:
        tickers = [esc(c.ticker) for c in res.stage2[:60]]
        more = f" 외 {len(res.stage2) - 60}" if len(res.stage2) > 60 else ""
        stage2_line = (f"<b>📋 Stage 2 통과 {len(res.stage2)}종목</b>\n"
                       f"<code>{' '.join(tickers)}</code>{more}")

    blocks = [
        head2,
        section("🆕 새로 등장한 셋업 (3일 이내)", fresh, with_exec=False, empty="없음"),
        section("💎 초타이트 엄선 (거래량 마름 + 마지막 수축 8% 이내)", tight, with_exec=False, empty="없음"),
    ]
    if stage2_line:
        blocks.append(stage2_line)
    blocks.append(
        f"<i>계좌 ${ACCOUNT:,.0f} · 1회 리스크 {RISK_PCT}% 기준 수량. "
        f"기술적 스크리닝이며 투자 조언이 아닙니다.</i>"
    )
    part2 = trim(blocks)

    return [part1, part2]


def main() -> int:
    cfg = Config()
    cfg.risk.account_size = ACCOUNT
    cfg.risk.risk_per_trade_pct = RISK_PCT

    try:
        res = screener.scan(cfg, universe_name="sp500", verbose=False)
    except Exception as e:
        telegram.send(f"⚠️ <b>미너비니 스크리너 실패</b>\n{esc(e)}")
        print(f"[minervini] 스캔 실패: {e}", file=sys.stderr)
        return 1

    ok = True
    for i, msg in enumerate(build_messages(res), 1):
        print(f"----- {i}편 ({len(msg)}자) -----\n{msg}\n")
        if not telegram.send(msg):
            ok = False
    print(f"[minervini] {res.scanned}종목 · Stage2 {len(res.stage2)} · {res.elapsed:.0f}초 · 전송 {ok}",
          file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
