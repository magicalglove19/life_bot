"""텔레그램 메시지 본문 작성.

폰으로는 종목명만 알면 된다. 조건별 판정과 일봉 흐름은 맥에서 리포트를 열어 본다.
  · 오늘 종가 매수  — 종목명
  · 정밀 진단       — 종목명과 점수 (점수 상위 몇 개만, 나머지는 맥에서 리포트로 본다)
  · 보유 종목       — 손절·익절 신호가 뜬 것만 (positions.txt 를 넘겼을 때)
  · 신정제 종가배팅 — 종목명 · 유형 · 재료 한 줄 · 손절선(당일 저가)
  · 일봉 스윙      — 종목명 · 타점 · 신고가/조정 · 손절선과 5일선
  · 세 줄 기법     — 종목명 · 조건 A/B · 기준봉 거래대금 · 재료 · 손절선
"""

from __future__ import annotations

import datetime as dt
import html

import numpy as np

from .config import Config

KST = dt.timezone(dt.timedelta(hours=9))


def _esc(x) -> str:
    return html.escape(str(x))


def _won(x: float) -> str:
    return f"{x:,.0f}" if np.isfinite(x) else "-"


def _closing_block(picks: list, total: int, cfg: Config) -> str:
    lines = ["🔔 <b>신정제 종가배팅</b>  <i>15:18~15:20 매수 · 다음 날 09:05 전 청산</i>"]
    if not picks:
        lines.append("없음")
        return "\n".join(lines)
    for p in picks:
        lines.append(f"<b>{_esc(p.name)}</b> ({_esc(p.code)}) 유형{p.kind} · "
                     f"{p.chg:+.1f}% · {p.value / 1e8:,.0f}억")
        if p.news:
            lines.append(f"  📰 {_esc(p.news[0][1][:40])}")
        elif not p.news_checked:
            lines.append("  📰 뉴스 조회 실패 — 재료 직접 확인")
        lines.append(f"  손절 매수가·당일저가 {_won(p.day_low)} 이탈")
    if total > len(picks):
        lines.append(f"<i>… 외 {total - len(picks)}종목</i>")
    lines.append("<i>15:18 체크: 저점 미이탈 · 1분봉 20선 눌림 반등 · 매도잔량&gt;매수잔량 · "
                 "프로그램 매수 · 추격 금지\n익일 09:00 시초가 1/3 → 09:05 전량 · "
                 "시간외 하락 시 전량 손절</i>")
    return "\n".join(lines)


def _swing_block(picks: list, total: int) -> str:
    lines = ["📊 <b>일봉 스윙</b>  <i>신고가 주도주 눌림목 · 5일선 살아있는 동안 보유</i>"]
    if not picks:
        lines.append("없음")
        return "\n".join(lines)
    for p in picks:
        lines.append(f"<b>{_esc(p.name)}</b> ({_esc(p.code)}) 타점{p.entry} · "
                     f"{p.chg:+.1f}% · {p.value / 1e8:,.0f}억")
        lines.append(f"  신고가 {p.peak_date} · {p.rest_bars}봉 조정 (-{p.depth:.0f}%)"
                     + (" · 주봉 O" if p.weekly_ok else ""))
        lines.append(f"  손절 {_won(p.stop_price)} ({_esc(p.stop_label)}, {p.stop_pct:+.1f}%) · "
                     f"5일선 {_won(p.trail_price)} 이탈 시 매도")
    if total > len(picks):
        lines.append(f"<i>… 외 {total - len(picks)}종목</i>")
    lines.append("<i>타점1은 1차 분할, 타점2가 메인. 5일선 살아있으면 손절선을 올리며 보유 · "
                 "대량거래 음봉/윗꼬리 뜨면 즉시 매도</i>")
    return "\n".join(lines)


def _threeline_block(picks: list, total: int) -> str:
    lines = ["📐 <b>세 줄 기법</b>  <i>기준봉 다음 날 종가(15:20~15:30) 매수</i>"]
    if not picks:
        lines.append("없음")
        return "\n".join(lines)
    for p in picks:
        shape = (f"갭상승 후 음봉 (갭 {p.gap:+.1f}%)" if p.entry == "A"
                 else f"윗꼬리가 길게 달린 캔들 (캔들의 {p.wick * 100:.0f}%)")
        lines.append(f"<b>{_esc(p.name)}</b> ({_esc(p.code)}) {p.chg:+.1f}%\n"
                     f"  2일차 조건{p.entry} · {shape}")
        lines.append(f"  기준봉 {p.base_date} {p.base_value / 1e8:,.0f}억 ({p.base_chg:+.0f}%) · "
                     f"{_esc(p.setup_label)}")
        if p.news:
            lines.append(f"  📰 {_esc(p.news[0][1][:40])}")
        elif not p.news_checked:
            lines.append("  📰 뉴스 조회 실패 — 재료 직접 확인")
        lines.append(f"  손절 기준봉 저가 {_won(p.base_low)} ({p.stop_pct:+.1f}%)"
                     + (f" · 섹터 동반 {p.sector_peers}종목" if p.sector_peers else ""))
    if total > len(picks):
        lines.append(f"<i>… 외 {total - len(picks)}종목</i>")
    lines.append("<i>못 샀으면 3일차 시초가. 수익 나면 즉시 익절, 타점 이탈하면 즉시 손절</i>")
    return "\n".join(lines)


def build(res, holdings: list, frames: dict, cfg: Config, detail_top: int = 8,
          closing: list | None = None, swing: list | None = None,
          threeline: list | None = None) -> str:
    # GitHub Actions 는 UTC 로 돈다. 국내장 도구이므로 항상 KST 로 찍는다.
    now = dt.datetime.now(KST).strftime("%m/%d %H:%M")
    head = f"매수 시그널 {len(res.buys)} · 조정 중 {len(res.tracking)}"
    if closing is not None:
        head += f" · 종가배팅 {len(closing)}"
    if swing is not None:
        head += f" · 스윙 {len(swing)}"
    if threeline is not None:
        head += f" · 세 줄 {len(threeline)}"
    blocks = [
        f"📈 <b>국내 스크리너</b>  {now}\n"
        f"<i>{cfg.market} {res.scanned}종목 · {head}</i>"
    ]
    if closing is not None:
        blocks.append(_closing_block(closing[:cfg.closing.top], len(closing), cfg))
    if swing is not None:
        blocks.append(_swing_block(swing[:cfg.swing.top], len(swing)))
    if threeline is not None:
        blocks.append(_threeline_block(threeline[:cfg.threeline.top], len(threeline)))

    if res.buys:
        lines = ["🎯 <b>오늘 종가 매수</b>"]
        for c in res.buys:
            lines.append(f"<b>{_esc(c.name)}</b> ({_esc(c.code)})")
        blocks.append("\n".join(lines))
    else:
        blocks.append("🎯 <b>오늘 종가 매수</b>\n없음")

    if res.tracking:
        lines = ["🔬 <b>정밀 진단</b>"]
        for c in res.tracking[:detail_top]:
            p = c.pattern or c.near_miss
            lines.append(f"{_esc(c.name)} ({_esc(c.code)}) {p.score:.0f}점")
        if len(res.tracking) > detail_top:
            lines.append(f"<i>… 외 {len(res.tracking) - detail_top}종목</i>")
        blocks.append("\n".join(lines))

    fired = [(c, s) for c in holdings for s in c.signals if s.kind in ("익절", "손절", "재진입")]
    if fired:
        lines = ["🚨 <b>보유 종목</b>"]
        for c, s in fired:
            lines.append(f"<b>{_esc(c.name)}</b> [{_esc(s.kind)}] {_esc(s.rule)} "
                         f"· {_won(c.price)}원")
        blocks.append("\n".join(lines))

    if len(holdings) > cfg.risk.max_positions:
        blocks.append(f"⚖️ 보유 {len(holdings)}종목 — 원칙 {cfg.risk.max_positions}개 이하입니다.")

    return "\n\n".join(blocks)
