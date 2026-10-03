"""평일 10:01~15:01 매시 — 듀퐁 1시간봉 박스 돌파 (국내, 관찰 단계).

방금 마감된 1시간봉 하나를 판정한다. 직전 7거래일 박스를 거래량 1.5배로 뚫고
20일선 위에서 마감하면 매수 신호. 신호는 가상으로 매매해 장부에 남기고,
1차 목표·손절·최종 목표·시간 청산·돌파 실패를 그때그때 알린다.
일어난 일이 없으면 아무것도 보내지 않는다.

  10:01 → 09시 봉   11:01 → 10시 봉   …   15:01 → 14시 봉 (장 마감 전 매수 가능)

판정 로직과 백테스트는 원본(2026 듀퐁 매매법/README.md)에 있다.
"""
import datetime as dt
import html
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd
import yfinance as yf

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram
from dupont import live, naver
from dupont.universe import BASE, ETFS

logging.getLogger("yfinance").setLevel(logging.CRITICAL)   # 코스닥을 .KS 로 먼저 찔러 보는 404 소음

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
STATE = Path(os.environ.get("DUPONT_STATE", ROOT / "data" / "dupont_state.json"))
SIM = os.environ.get("DUPONT_SIM") == "1"     # --asof 재현 때 상태 파일을 이어 쓴다 (발송은 안 함)
CACHE = Path(__file__).resolve().parent / "cache" / "dupont"

MAX_PER_DAY = int(os.environ.get("DUPONT_MAX_PER_DAY", "2"))   # 하루 새 신호 상한 (거래량 배수 큰 순)
WORKERS = 4

# 실행 창. 마지막 판정은 15:01 (14시 봉), 15시 봉 관리는 다음 날 10:01 에 반영된다.
WINDOW_START = (10, 0)
WINDOW_END = (15, 45)
# 방금 마감된 봉이 이보다 오래됐으면 새 신호를 내지 않는다 (트리거가 늦게 왔을 때 지난 봉으로 신호 금지).
STALE_MIN = 50


# ── 상태 ─────────────────────────────────────────────

def load_state() -> dict:
    try:
        return json.loads(STATE.read_text(encoding="utf-8"))
    except Exception:
        return {"trades": [], "signals": {}}


def save_state(st: dict) -> None:
    # 끝난 거래는 최근 200건만 남긴다 (장부 요약용)
    open_ = [t for t in st["trades"] if t["status"] == "open"]
    done = [t for t in st["trades"] if t["status"] != "open"][-200:]
    st["trades"] = done + open_
    days = sorted(st["signals"])[-30:]
    st["signals"] = {d: st["signals"][d] for d in days}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


# ── 종목 ─────────────────────────────────────────────

def yahoo_hourly(code: str, suffix: str | None, today: dt.date) -> pd.DataFrame:
    """어제까지의 1시간봉 (야후, 60일). 하루 한 번 받아 캐시한다."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"{code}_{today:%Y%m%d}.pkl"
    if path.exists():
        return pd.read_pickle(path)
    df = pd.DataFrame()
    for sfx in ([suffix] if suffix else ["KS", "KQ"]):
        try:
            df = yf.Ticker(f"{code}.{sfx}").history(period="60d", interval="1h", auto_adjust=False)
        except Exception:
            df = pd.DataFrame()
        if not df.empty:
            break
    if not df.empty:
        df = df[["Open", "High", "Low", "Close", "Volume"]].dropna()
        df = df[df["Volume"] > 0]
        df.index = df.index.tz_convert(KST)
    df.to_pickle(path)
    return df


def fetch(code: str, suffix: str | None, now: dt.datetime):
    try:
        y = yahoo_hourly(code, suffix, now.date())
        n = naver.hourly(code, now)
    except Exception as e:
        print(f"[dupont] {code} 시세 실패: {e}", file=sys.stderr)
        return code, None
    bars = live.merge_bars(y, n, now.date())
    return code, live.completed(bars, now)


# ── 메시지 ───────────────────────────────────────────

def won(x: float) -> str:
    return f"{x:,.0f}"


def pct(a: float, b: float) -> str:
    return f"{(a / b - 1) * 100:+.1f}%"


def signal_text(name, code, tag, sig) -> str:
    risk = sig["close"] - sig["poc"]
    h = sig["time"].hour
    lines = [
        f"🟢 <b>{html.escape(name)}</b> {code}{tag}",
        f"{h}시 봉 종가 {won(sig['close'])} — 7일 박스 상단 {won(sig['box_hi'])} 돌파",
        f"거래량 {sig['rv_box']:.1f}배{' 💪' if sig['rv_box'] >= 2 else ''} · 박스 폭 {sig['width_atr']:.1f} ATR"
        f" · POC 왕복 {sig['cross']}회",
        f"손절 {won(sig['poc'])} (중앙선, {pct(sig['poc'], sig['close'])})",
        f"1차 {won(sig['close'] + risk)} (+1R, 50%) · 최종 {won(sig['close'] + 3 * risk)} (+3R)",
    ]
    return "\n".join(lines)


def event_text(tr: dict, kind: str, info: dict) -> str:
    nm = f"<b>{html.escape(tr['name'])}</b>"
    h = info["time"].hour
    if kind == "fake":
        return (f"⚠️ {nm} 돌파 실패 경고 — {h}시 봉 종가 {won(info['close'])} 가 "
                f"박스 상단 {won(tr['level'])} 아래 (손절가 {won(tr['poc'])} 은 그대로)")
    if kind == "cancel":
        return f"⛔ {nm} 진입 취소 — 다음 봉 시가가 이미 중앙선 아래"
    if kind == "t1":
        return (f"🎯 {nm} 1차 목표 {won(tr['t1'])} 도달 ({h}시 봉) — 50% 익절, "
                f"나머지 손절을 진입가 {won(tr['entry'])} 로")
    icon = {"최종목표": "✅", "시간청산": "⏱", "손절": "🛑", "갭손절": "🛑"}.get(tr["exit_reason"], "➖")
    extra = " — 오늘 종가 전 정리" if tr["exit_reason"] == "시간청산" else ""
    return (f"{icon} {nm} {tr['exit_reason']} {won(tr['exit_price'])} ({h}시 봉) · "
            f"<b>{tr['r']:+.2f}R</b>{extra}")


def ledger(st: dict) -> str:
    done = [t for t in st["trades"] if t["status"] == "closed"]
    op = [t for t in st["trades"] if t["status"] == "open"]
    if not done:
        return f"📒 가상 장부: 종료 0건 · 보유 {len(op)}건"
    r = [t["r"] for t in done]
    win = sum(x > 0 for x in r) / len(r) * 100
    return (f"📒 가상 장부: 종료 {len(r)}건 · 승률 {win:.0f}% · 평균 {sum(r) / len(r):+.2f}R"
            f" · 누적 {sum(r):+.1f}R · 보유 {len(op)}건")


# ── 실행 ─────────────────────────────────────────────

def main() -> int:
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv or os.environ.get("DUPONT_FORCE") == "1"
    now = dt.datetime.now(KST)
    asof = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--asof=")), None)
    if asof:                                  # 테스트용: --asof=2026-10-02T11:01 (자동으로 --dry-run)
        now = dt.datetime.fromisoformat(asof).replace(tzinfo=KST)
        dry = True
    if not (dry or force):
        if now.weekday() > 4:
            print(f"[dupont] 주말({now:%m/%d}) — 건너뜁니다.")
            return 0
        if not (WINDOW_START <= (now.hour, now.minute) <= WINDOW_END):
            print(f"[dupont] {now:%H:%M} KST 는 실행 창 밖 — 건너뜁니다.")
            return 0

    st = load_state()
    today = f"{now:%Y-%m-%d}"
    sent_today = st["signals"].setdefault(today, [])

    names = {c: (n, s, "") for c, (n, s) in BASE.items()}
    # 감시 목록에서 빠진 종목도 보유 중이면 끝까지 추적한다
    for tr in st["trades"]:
        if tr["status"] == "open" and tr["code"] not in names:
            names[tr["code"]] = (tr["name"], None, "")
    with ThreadPoolExecutor(WORKERS) as ex:
        bars = dict(ex.map(lambda c: fetch(c, names[c][1], now), names))
    ok = {c: b for c, b in bars.items() if b is not None and not b.empty}
    print(f"[dupont] {now:%H:%M} 시세 {len(ok)}/{len(names)}종목", flush=True)

    # 1) 보유 중인 가상 매매 관리
    events = []
    for tr in st["trades"]:
        if tr["status"] == "open" and tr["code"] in ok:
            for kind, info in live.step(tr, ok[tr["code"]]):
                events.append(event_text(tr, kind, info))

    # 2) 방금 마감된 봉 판정
    holding = {t["code"] for t in st["trades"] if t["status"] == "open"}
    cands, checked = [], 0
    for code, b in ok.items():
        reg = live.regular(b)
        if reg.empty or reg.index[-1].date() != now.date():
            continue
        end = reg.index[-1].to_pydatetime() + dt.timedelta(hours=1)
        if (now - end).total_seconds() / 60 > STALE_MIN and not force:
            continue
        key = f"{code}|{reg.index[-1]:%H}"
        if code in holding or key in sent_today:
            continue
        sig = live.evaluate(reg, len(reg) - 1)
        checked += 1
        if sig and sig["signal"]:
            cands.append((code, sig, key))
    cands.sort(key=lambda c: -c[1]["rv_box"])
    room = max(0, MAX_PER_DAY - sum(1 for k in sent_today if not k.startswith("~")))
    take, skipped = cands[:room], cands[room:]
    print(f"[dupont] 판정 {checked}종목 · 신호 {len(cands)} · 발송 {len(take)} · 상한 초과 {len(skipped)}"
          f" · 보유 이벤트 {len(events)}", flush=True)

    sig_texts = []
    for code, sig, key in take:
        name, _, tag = names[code]
        sig_texts.append(signal_text(name, code, tag, sig))
        st["trades"].append(live.new_trade(code, name, tag.strip(" ·"), sig))
        sent_today.append(key)
    for code, sig, key in skipped:
        sent_today.append("~" + key)          # 상한으로 거른 것도 같은 봉으로 다시 판정하지 않는다

    if not (sig_texts or events):
        if dry:
            print("[dupont] 보낼 내용 없음\n" + ledger(st))
        if not dry or SIM:
            save_state(st)
        return 0

    parts = [f"📐 <b>듀퐁 박스 돌파</b> {now:%m/%d %H:%M} · 🧪 관찰 단계(가상 매매)"]
    if sig_texts:
        parts.append("\n\n".join(sig_texts))
        if skipped:
            extra = ", ".join(html.escape(names[c][0]) for c, _, _ in skipped)
            parts.append(f"<i>하루 상한으로 제외: {extra}</i>")
    if events:
        parts.append("\n".join(events))
    parts.append(ledger(st))
    body = "\n\n".join(parts)

    if dry:
        print(body)
        if SIM:
            save_state(st)
        return 0
    if not telegram.send(body):
        save_state(st)          # 판정·장부는 남긴다 (같은 봉을 다시 보내지 않도록)
        return 1
    save_state(st)
    print("[dupont] 발송 완료")
    return 0


if __name__ == "__main__":
    sys.exit(main())
