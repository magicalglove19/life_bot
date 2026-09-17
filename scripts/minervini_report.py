"""월~토 05:30 KST — 마크 미너비니 SEPA 스크리너 (기본 S&P 500, MINERVINI_UNIVERSE 로 변경).

아침 브리핑(morning-stocks, 05:00 KST)과 같은 시간대에 도착한다.
미국장 마감 직후라 당일 종가가 확정된 상태로 판정한다.

메시지가 길어 잘리는 것을 막기 위해 항상 2편으로 나눠 보낸다.
  1편 — 시장 국면 + 지금 실행 가능한 자리 (당일 돌파 / 최근 돌파 / 매수구간)
  2편 — 신규 셋업 · 초타이트 엄선 · Stage 2 요약

'당일 돌파'는 오늘 피벗을 대량 거래로 넘어선 것만 따로 뺀다. 점수 하한을 적용하지
않는다 — 점수가 낮아도 오늘 터졌으면 봐야 하는 자리이기 때문이다.

review.py 로 최근 1년을 되감아 재보니 두 자리의 성적이 크게 갈렸다.
  거래량 확인된 돌파   39건 · 승률 64% · 손익비 1.74 · SPY 대비 +3.49%
  아직 안 터진 매수구간 357건 · 승률 42% · 손익비 1.18 · SPY 대비 -0.92%
그래서 돌파에는 1회 리스크를 다 걸고(1.0배) 미확인 자리는 절반만 건다(0.5배).
메시지에도 이 근거를 같이 실어서, 어느 목록을 믿을지 매일 보이게 한다.

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
from minervini import qullamaggie, report, screener
from minervini.universe import UNIVERSE_TITLE
from minervini.config import Config

KST = dt.timezone(dt.timedelta(hours=9))

# 계좌 설정 — 수량 계산에만 쓰인다. 워크플로 env로 덮어쓸 수 있다.
ACCOUNT = float(os.environ.get("MINERVINI_ACCOUNT", "100000"))
RISK_PCT = float(os.environ.get("MINERVINI_RISK", "1.25"))
MIN_SCORE = float(os.environ.get("MINERVINI_MIN_SCORE", "75"))
# 스캔 대상. 기본 S&P 500. sp1500 이면 중·소형까지 (4년 실측상 신호는 3배, 평균 성과는 낮음)
UNIVERSE = os.environ.get("MINERVINI_UNIVERSE", "sp500")

CFG_MIN_RS = Config().trend.min_rs_rating   # 화면 설명용 (config.py 가 진짜 기준)

MAX_PART_CHARS = 3400   # 텔레그램 4096자 한도에 여유를 둔 편당 상한
SECTION_LIMIT = 8       # 섹션당 최대 종목 수
TIGHT_LAST_DEPTH = 8.0  # 초타이트 판정: 마지막 수축 %
MAX_DAYS_PAST_PIVOT = 5  # 피벗을 넘은 지 이 거래일을 넘기면 연장(쫓아가는 매수)
MAX_PCT_ABOVE_PIVOT = 5.0  # 피벗 위로 이 %를 넘게 올라가 있어도 연장 (오늘 돌파라도)

LIGHT_EMOJI = {"초록불": "🟢", "노란불": "🟡", "주황불": "🟠", "빨간불": "🔴", "회색불": "⚪"}

# 최근 1년 되감기 실적 (python3 review.py --months 12 로 다시 뽑는다)
BACKTEST_BREAKOUT = "1년 39건 · 승률 64% · 평균 +4.32% · SPY 대비 +3.49%"
BACKTEST_SETUP = "1년 357건 · 승률 42% · 평균 -0.38% · SPY 대비 -0.92%"


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
    """매수 타점(피벗)을 넘은 지 며칠, 피벗보다 얼마나 위인가."""
    if c.days_past_pivot < 0:
        return "피벗 대기"
    txt = "오늘 넘음" if c.days_past_pivot == 0 else f"타점 {c.days_past_pivot}일 경과"
    if np.isfinite(c.pct_above_pivot):
        txt += f" (피벗 +{c.pct_above_pivot:.1f}%)"
    return txt + (" ⚠연장" if c.extended else "")


def is_extended(c) -> bool:
    """날짜(며칠 지났나) 또는 거리(피벗에서 얼마나 올랐나)로 타점을 지나쳤는지. screener가 판정한다."""
    return c.extended


def stock_line(c, with_exec: bool = True) -> str:
    """종목 한 건. 실행 정보가 붙으면 2줄, 아니면 1줄."""
    q = []
    if c.growth_ok is True:
        q.append("실적✓")
    elif c.growth_ok is False:
        q.append("실적✗")
    if np.isfinite(c.ud_ratio):
        q.append(f"매집 {num(c.ud_ratio, 2)}")
    head = (f"• <b>{esc(c.ticker)}</b> ${num(c.price)} · RS {num(c.rs_rating, 0)}"
            f" · 점수 {num(c.total_score, 0)}" + (" · " + " ".join(q) if q else ""))
    if not with_exec:
        # 관심 목록 — 지금 실행할 자리인지(상태)를 같이 보여준다.
        bits = [esc(c.vcp.status), pivot_age(c)]
        if c.setup_days:
            bits.append(f"셋업 {c.setup_days}일째")
        return head + "\n   " + " · ".join(bits)

    stop_txt = f"{num(c.stop)} ({num(-c.stop_pct, 1)}%)" if np.isfinite(c.stop_pct) else num(c.stop)
    detail = f"   진입 {num(c.entry)} / 손절 {stop_txt}"
    if c.shares:
        detail += f" · 수량 {c.shares}"
        if c.risk_mult != 1.0:
            detail += f" (비중 {num(c.risk_mult, 1)}배)"
    detail += f" · {pivot_age(c)}"
    if np.isfinite(c.vcp.breakout_volume_mult):
        detail += f" · 돌파거래량 {num(c.vcp.breakout_volume_mult, 2)}x"
    return f"{head}\n{detail}"


def section(title: str, items: list, with_exec: bool = True, empty: str = "해당 없음",
            note: str | None = None) -> str:
    """note 는 제목 아래 한 줄로 붙는 보조 설명 (볼드에 먹히지 않게 따로 뺀다)."""
    lines = [f"<b>{title}</b> ({len(items)})" if items else f"<b>{title}</b>"]
    if note:
        lines.append(f"<i>{note}</i>")
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
        # 시장 폭은 RS 70 고정 기준으로 잰 '시장의 상태'다.
        # 위 헤더의 'Stage 2 통과 N'(내 RS 하한으로 거른 수)과는 다른 숫자다.
        lines.append(f"52주 고점 대비 {num(r.pct_from_high, 1)}% · "
                     f"시장 폭 {num(r.breadth_stage2, 1)}% · "
                     f"200일선 위 {num(r.breadth_above_ma200, 0)}%")
        lines.append(f"<i>시장 폭 = 전체 중 상승추세 비율(RS 70 고정). "
                     f"내 기준(RS {num(CFG_MIN_RS, 0)})으로 거른 종목 수와는 다릅니다.</i>")
    # 만기 3거래일 전부터만 한 줄 (5년 실측상 손익비 차이가 단정할 수준이 아니라 설명은 생략)
    if getattr(r, "opex_date", None) is not None and 0 <= r.opex_days <= OPEX_NOTICE_DAYS:
        import pandas as pd

        d = pd.Timestamp(r.opex_date)
        when = "오늘" if r.opex_days == 0 else f"{r.opex_days}거래일 뒤"
        quad = " · 쿼드위칭" if r.opex_quad else ""
        lines.append(f"📅 옵션 만기 <b>{d:%m/%d}</b> ({when}){quad}")
    if r.comment:
        lines.append(f"<i>{esc(r.comment)}</i>")
    if report.red_light(r):
        lines.append(f"<b>{esc(report.RED_WARNING)}</b>")
    return "\n".join(lines)


def trim(parts: list[str]) -> str:
    """편 하나가 상한을 넘지 않도록 뒤쪽 섹션부터 잘라낸다."""
    text = "\n\n".join(parts)
    while len(text) > MAX_PART_CHARS and len(parts) > 2:
        parts = parts[:-1]
        text = "\n\n".join(parts + ["<i>…이하 생략 (길이 제한)</i>"])
    return text


def build_messages(res) -> list[str]:
    setups = [c for c in res.candidates if c.vcp.is_vcp]
    cands = [c for c in setups if c.total_score >= MIN_SCORE]

    # 당일 돌파는 점수 하한을 적용하지 않는다 — 오늘 터진 자리는 점수와 무관하게 봐야 한다.
    today_brk = [c for c in setups
                 if c.vcp.status == "돌파" and c.days_past_pivot == 0 and not is_extended(c)]
    recent_brk = [c for c in setups
                  if c.vcp.status == "돌파" and 0 < c.days_past_pivot and not is_extended(c)]
    stale_brk = [c for c in setups if c.vcp.status == "돌파" and is_extended(c)]

    buyzone = [c for c in cands if c.vcp.status == "매수구간"]
    fresh = [c for c in cands if c.setup_days and c.setup_days <= 3]
    tight = [c for c in cands
             if c.vcp.depths and c.vcp.depths[-1] <= TIGHT_LAST_DEPTH
             and np.isfinite(c.vcp.dryup_ratio) and c.vcp.dryup_ratio <= 0.85]

    now = dt.datetime.now(KST)
    stamp = now.strftime("%m/%d (%a) %H:%M")

    # ---------- 1편: 국면 + 지금 실행할 자리 ----------
    head1 = (f"🇺🇸 <b>미너비니 스크리너</b> · {stamp}  <b>(1/2)</b>\n"
             f"{esc(UNIVERSE_TITLE.get(UNIVERSE, UNIVERSE))} {res.scanned}종목 스캔 · RS {num(CFG_MIN_RS, 0)}+ Stage 2 통과 {len(res.stage2)}"
             f" · 종합 {num(MIN_SCORE, 0)}점 이상 {len(cands)}종목")
    blocks1 = [
        head1,
        market_block(res.regime),
        section("⚡ 오늘 돌파 — 리스크 1.0배 (점수 무관)", today_brk,
                empty="오늘 새로 돌파한 종목 없음", note=BACKTEST_BREAKOUT),
        section(f"🚀 최근 돌파 (1~{MAX_DAYS_PAST_PIVOT}일 전 · 피벗 +{MAX_PCT_ABOVE_PIVOT:.0f}% 이내)", recent_brk,
                empty="없음 — 무리해서 쫓아가지 않는다"),
        section("🎯 매수구간 대기 — 리스크 0.5배만 (피벗 6% 이내)", buyzone,
                empty="없음", note=BACKTEST_SETUP),
    ]
    if stale_brk:
        names = ", ".join(f"{esc(c.ticker)}({esc(c.extended_reason)})" for c in stale_brk[:8])
        blocks1.append(f"<i>연장이라 제외 — {MAX_DAYS_PAST_PIVOT}일 초과 또는 피벗 +{MAX_PCT_ABOVE_PIVOT:.0f}% 초과: {names}</i>")
    part1 = trim(blocks1)

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
        section("💎 초타이트 엄선 (마지막 수축 8% 이내 + 거래량 마름)", tight, with_exec=False,
                empty="없음"),
    ]
    if stage2_line:
        blocks.append(stage2_line)
    blocks.append(
        f"<i>💎는 베이스 '모양'이 좋다는 뜻(품질)이고, 지금 살 자리인지는 각 줄의 상태·타점으로 봅니다.\n"
        f"계좌 ${ACCOUNT:,.0f} · 1회 리스크 {RISK_PCT}% 기준 수량. "
        f"거래량으로 확인된 돌파는 리스크를 다 걸고(1.0배), 아직 피벗을 못 넘은 자리는 "
        f"절반만 겁니다(0.5배) — 1년 되감기에서 두 자리의 성적이 크게 갈렸습니다.\n"
        f"기술적 스크리닝이며 투자 조언이 아닙니다.</i>"
    )
    part2 = trim(blocks)

    return [part1, part2]


# 쿨라매기 3중 이평 — 미너비니와 별개 전략이라 3편으로 따로 보낸다.
# 백테스트: 2026 마크미니 스크리너/backtest_qull.py --years 5 --compare (S&P 500 현 구성종목, 롱)
QULL_ON = os.environ.get("MINERVINI_QULL", "1") != "0"
OPEX_NOTICE_DAYS = 3   # 옵션 만기 안내를 띄우기 시작할 남은 거래일
QULL_BACKTEST_EP = "EP 5년 474건 · 승률 37% · 거래당 +2.72% · PF 2.03"
QULL_BACKTEST = "5년 786건 · 승률 38% · 거래당 +1.11% · 하락장(2022)엔 손실"
QULL_LIMIT = 8


def build_qull_message(res, cfg) -> str:
    out = qullamaggie.scan_today(res.frames, res.names, cfg)
    stamp = dt.datetime.now(KST).strftime("%m/%d (%a) %H:%M")
    mine = {c.ticker: f"{c.vcp.status} {c.total_score:.0f}점" for c in res.candidates
            if c.vcp.is_vcp and c.total_score >= MIN_SCORE}
    s2 = {c.ticker for c in res.stage2}

    def tag(tk):
        if tk in mine:
            return f" · <i>미너비니 {esc(mine[tk])}</i>"
        return " · <i>미너비니 Stage 2</i>" if tk in s2 else ""

    lines = [f"🏄 <b>쿨라매기 3중 이평</b> · {stamp}  <b>(3/3)</b>",
             "<i>미너비니와 별개 전략 — 10EMA&gt;20EMA&gt;50SMA · 수축+거래량 마름 · 종가 돌파 · 청산 종가&lt;20EMA</i>"]
    if report.red_light(res.regime):
        lines.append(f"<b>{esc(report.RED_WARNING)}</b>")

    L = out["long_today"]
    lines.append(f"\n<b>🏄 오늘 롱 신호</b> ({len(L)})" if L else "\n<b>🏄 오늘 롱 신호</b>")
    if L:
        for p in L[:QULL_LIMIT]:
            lines.append(f"• <b>{esc(p.ticker)}</b> ${num(p.price)} · 돌파거래량 {num(p.vol_ratio, 1)}x{tag(p.ticker)}\n"
                         f"   진입 {num(p.price)} / 손절 {num(p.stop)} (-{num(p.stop_pct, 1)}%) · 청산선 20EMA {num(p.exit_line)}"
                         + (f" · 수량 {p.shares}" if p.shares else ""))
    else:
        lines.append("오늘 조건을 모두 채운 돌파 없음")
    lines.append(f"<i>백테스트 {QULL_BACKTEST}</i>")

    E = out["ep_today"]
    lines.append(f"\n<b>⚡ EP — 갭상승 촉매</b> ({len(E)})" if E else "\n<b>⚡ EP — 갭상승 촉매</b>")
    if E:
        for p in E[:QULL_LIMIT]:
            lines.append(f"• <b>{esc(p.ticker)}</b> ${num(p.price)} · 갭 +{num(p.gap_pct, 1)}% · 거래량 {num(p.vol_ratio, 1)}x{tag(p.ticker)}\n"
                         f"   진입 {num(p.price)} / 손절 {num(p.stop)} (당일 저점, -{num(p.stop_pct, 1)}%)"
                         + (f" · 수량 {p.shares}" if p.shares else ""))
    else:
        lines.append("오늘 갭상승 촉매 없음")
    lines.append(f"<i>백테스트 {QULL_BACKTEST_EP}</i>")

    W = out["watch"]
    lines.append(f"\n<b>👀 돌파 대기</b> ({len(W)}) — 트리거 5% 이내" if W else "\n<b>👀 돌파 대기</b>")
    if W:
        for p in W[:QULL_LIMIT]:
            note = " · 첫반등 스킵 대상" if p.first_in_regime else ""
            lines.append(f"• <b>{esc(p.ticker)}</b> ${num(p.price)} → 트리거 {num(p.trigger)} (+{num(p.dist_pct, 1)}%)"
                         f" · 손절 {num(p.stop)}{note}{tag(p.ticker)}")
        if len(W) > QULL_LIMIT:
            lines.append(f"   … 외 {len(W) - QULL_LIMIT}건")
    else:
        lines.append("없음")

    O = out["open_long"]
    if O:
        lines.append(f"\n<b>📌 규칙상 보유 중</b> ({len(O)}) — 종가가 청산선 아래면 청산")
        for p in O[:QULL_LIMIT]:
            gap = (p.exit_line / p.price - 1) * 100 if p.price else np.nan
            lines.append(f"• <b>{esc(p.ticker)}</b> [{'EP' if p.setup == 'ep' else '돌파'}] {p.entry_date:%m/%d} 진입 {num(p.trigger)} → ${num(p.price)}"
                         f" ({num(p.open_ret_pct, 1)}%) · 청산선 {num(p.exit_line)} ({num(gap, 1)}%)")

    if out["short_today"]:
        lines.append(f"\n<i>숏 신호(참고, 백테스트 손실): {', '.join(esc(p.ticker) for p in out['short_today'][:10])}</i>")
    # 넘치면 줄 단위로 뒤에서부터 뺀다 — 글자 단위로 자르면 HTML 태그가 끊겨 텔레그램이 거부한다
    while len("\n".join(lines)) > MAX_PART_CHARS and len(lines) > 3:
        lines.pop()
        if lines[-1] != "<i>…이하 생략 (길이 제한)</i>":
            lines.append("<i>…이하 생략 (길이 제한)</i>")
            if len("\n".join(lines)) > MAX_PART_CHARS:
                lines.pop(-2)
    return "\n".join(lines)


def main() -> int:
    dry = "--dry-run" in sys.argv    # 텔레그램으로 보내지 않고 화면에만 출력
    cfg = Config()
    cfg.risk.account_size = ACCOUNT
    cfg.risk.risk_per_trade_pct = RISK_PCT

    cfg.vcp.max_days_past_pivot = MAX_DAYS_PAST_PIVOT
    cfg.vcp.max_pct_above_pivot = MAX_PCT_ABOVE_PIVOT

    try:
        res = screener.scan(cfg, universe_name=UNIVERSE, verbose="--verbose" in sys.argv)
    except Exception as e:
        if not dry:
            telegram.send(f"⚠️ <b>미너비니 스크리너 실패</b>\n{esc(e)}")
        print(f"[minervini] 스캔 실패: {e}", file=sys.stderr)
        return 1

    ok = True
    messages = build_messages(res)
    if QULL_ON:
        try:
            messages.append(build_qull_message(res, cfg))
        except Exception as e:                  # 쿨라매기가 실패해도 미너비니 1·2편은 그대로 보낸다
            print(f"[minervini] 쿨라매기 3편 생성 실패: {e}", file=sys.stderr)
        else:
            messages = [m.replace("<b>(1/2)</b>", "<b>(1/3)</b>").replace("<b>(2/2)</b>", "<b>(2/3)</b>")
                        for m in messages]
    for i, msg in enumerate(messages, 1):
        print(f"----- {i}편 ({len(msg)}자) -----\n{msg}\n")
        if dry:
            continue
        if not telegram.send(msg):
            ok = False
    if dry:
        print("[minervini] --dry-run: 텔레그램으로 보내지 않았습니다.", file=sys.stderr)
    print(f"[minervini] {res.scanned}종목 · Stage2 {len(res.stage2)} · {res.elapsed:.0f}초 · 전송 {ok}",
          file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
