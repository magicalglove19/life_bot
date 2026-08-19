"""월~금 14:00 KST — 유목민식 8일선/정배열 스크리너.

패턴 A: 정배열 상태에서 5일선을 잠깐 이탈했다가 8일선에서 지지받는 종목
패턴 B: 역배열에서 정배열로 막 전환되기 직전인 종목 (골든크로스 임박)

유니버스: 코스피+코스닥 시가총액 상위 300 (우선주/스팩/관리종목 제외)
데이터: FinanceDataReader

장중 14시에 도는 작업이라 당일 봉은 아직 확정 종가가 아님 → 잠정 결과로 표시.
"""
import os
import re
import sys
import time
import socket
import threading
import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

socket.setdefaulttimeout(15)

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram

# ----------------------------------------------------------------------
# 파라미터 (로컬 screener.py와 동일하게 유지할 것)
# ----------------------------------------------------------------------
UNIVERSE_SIZE = int(os.environ.get("SCREENER_UNIVERSE", "300"))
MA_PERIODS = [5, 8, 10, 20, 60, 120]
HISTORY_DAYS = 420
MAX_WORKERS = 10
PER_CALL_TIMEOUT = 20        # 종목 1개당 강제 시간제한(초)
MAX_FETCH_SEC = 900          # 수집 전체 예산(초). 넘으면 확보된 것만으로 스크리닝
TOP_N = 15                   # 패턴별 메시지 표시 개수

# 패턴 A
A_PRIOR_UPTREND_LOOKBACK = 15
A_BREAK_SEARCH_WINDOW = 6
A_MA10_TOLERANCE = 0.985
A_MA8_BAND = 0.02
A_STRUCT_TOLERANCE = 0.995

# 패턴 B
B_RATIO_LOW, B_RATIO_HIGH = 0.95, 1.03
B_CONVERGE_LOOKBACK = 20
B_WAS_BEARISH_THRESHOLD = 0.92
B_WAS_BEARISH_LOOKBACK = 60
B_MA20_RISE_LOOKBACK = 5

KST = dt.timezone(dt.timedelta(hours=9))
MARKET_CLOSE_MIN = 15 * 60 + 40   # 15:40 KST — 마감(15:30) + 데이터 반영 여유

TODAY = dt.datetime.now(KST).date()
START_DATE = (TODAY - dt.timedelta(days=HISTORY_DAYS)).isoformat()

PREFERRED_STOCK_RE = re.compile(r".*\d?우(B|C)?$")
EXCLUDE_DEPT = {
    "SPAC(소속부없음)",
    "관리종목(소속부없음)",
    "투자주의환기종목(소속부없음)",
    "외국기업(소속부없음)",
}


# ----------------------------------------------------------------------
# 데이터 수집
# ----------------------------------------------------------------------
def get_universe(n=UNIVERSE_SIZE):
    import FinanceDataReader as fdr

    df = fdr.StockListing("KRX")
    df = df[df["Market"].isin(["KOSPI", "KOSDAQ"])]
    df = df[~df["Dept"].isin(EXCLUDE_DEPT)]
    df = df[~df["Name"].apply(lambda x: bool(PREFERRED_STOCK_RE.match(str(x))))]
    df = df[df["Marcap"] > 0]
    df = df.sort_values("Marcap", ascending=False).head(n)
    return df[["Code", "Name", "Market", "Marcap"]].reset_index(drop=True)


def _fetch_raw(code, retries, holder):
    """실제 네트워크 호출. 데몬 스레드에서 돌며, 응답이 아무리 느려도
    호출부는 PER_CALL_TIMEOUT 이후 버려두고 다음으로 진행한다."""
    import FinanceDataReader as fdr

    for _ in range(retries + 1):
        try:
            df = fdr.DataReader(code, START_DATE)
            if df is not None and len(df) >= 70:
                holder["df"] = df
                return
        except Exception:
            continue
    holder["df"] = None


def fetch_one(code, retries=1):
    holder = {}
    t = threading.Thread(target=_fetch_raw, args=(code, retries, holder), daemon=True)
    t.start()
    t.join(PER_CALL_TIMEOUT)
    if t.is_alive():
        return code, None
    return code, holder.get("df")


def fetch_all(codes, deadline):
    result = {}
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futs = {ex.submit(fetch_one, c): c for c in codes}
        done, total = 0, len(codes)
        for fut in as_completed(futs):
            code, df = fut.result()
            if df is not None:
                result[code] = df
            done += 1
            if done % 50 == 0 or done == total:
                print(f"  ...{done}/{total} 종목 수집", file=sys.stderr)
            if time.time() > deadline:
                print(f"[ma8] 수집 예산 {MAX_FETCH_SEC}초 초과 → {len(result)}개로 진행", file=sys.stderr)
                for f in futs:
                    f.cancel()
                break
    return result


# ----------------------------------------------------------------------
# 지표 & 패턴 판정 (로컬 screener.py와 동일 로직)
# ----------------------------------------------------------------------
def add_mas(df):
    df = df.copy()
    for p in MA_PERIODS:
        df[f"MA{p}"] = df["Close"].rolling(p).mean()
    return df


def check_pattern_a(df):
    if len(df) < max(A_PRIOR_UPTREND_LOOKBACK, 60) + 5:
        return None
    d = df.dropna(subset=[f"MA{p}" for p in (5, 8, 10, 20, 60)])
    if len(d) < A_PRIOR_UPTREND_LOOKBACK + A_BREAK_SEARCH_WINDOW + 2:
        return None

    last_n = d.tail(A_PRIOR_UPTREND_LOOKBACK + A_BREAK_SEARCH_WINDOW + 1)

    full_align = (
        (last_n["Close"] > last_n["MA5"])
        & (last_n["MA5"] > last_n["MA10"])
        & (last_n["MA10"] > last_n["MA20"])
        & (last_n["MA20"] > last_n["MA60"])
    )
    if not full_align.head(A_PRIOR_UPTREND_LOOKBACK).any():
        return None

    recent = d.tail(A_BREAK_SEARCH_WINDOW + 1)
    below5 = recent["Close"] < recent["MA5"]
    prev_above5 = recent["Close"].shift(1) >= recent["MA5"].shift(1)
    break_events = recent[below5 & prev_above5]
    if break_events.empty:
        if not below5.tail(1).iloc[0]:
            return None
        break_day = recent.index[below5][0]
    else:
        break_day = break_events.index[-1]

    since_break = d.loc[break_day:]
    if len(since_break) == 0:
        return None

    if (since_break["Close"] < since_break["MA10"] * A_MA10_TOLERANCE).any():
        return None

    today = d.iloc[-1]

    gap8 = (today["Close"] - today["MA8"]) / today["MA8"]
    if abs(gap8) > A_MA8_BAND:
        return None

    tol = A_STRUCT_TOLERANCE
    if not (
        today["MA8"] > today["MA10"] * tol
        and today["MA10"] > today["MA20"] * tol
        and today["MA20"] > today["MA60"] * tol
    ):
        return None

    return {
        "break_day": break_day.date().isoformat(),
        "days_since_break": len(since_break) - 1,
        "gap_to_ma8_pct": round(gap8 * 100, 2),
        "close": today["Close"], "ma5": round(today["MA5"], 1), "ma8": round(today["MA8"], 1),
        "ma10": round(today["MA10"], 1), "ma20": round(today["MA20"], 1), "ma60": round(today["MA60"], 1),
        "last_bar": d.index[-1].date(),
    }


def check_pattern_b(df):
    if len(df) < B_WAS_BEARISH_LOOKBACK + 10:
        return None
    d = df.dropna(subset=[f"MA{p}" for p in (5, 10, 20, 60)])
    if len(d) < B_WAS_BEARISH_LOOKBACK + 5:
        return None

    d = d.copy()
    d["ratio2060"] = d["MA20"] / d["MA60"]

    today = d.iloc[-1]
    ratio_now = today["ratio2060"]

    if not (B_RATIO_LOW <= ratio_now <= B_RATIO_HIGH):
        return None

    past_window = d["ratio2060"].tail(B_WAS_BEARISH_LOOKBACK)
    if past_window.min() >= B_WAS_BEARISH_THRESHOLD:
        return None

    conv_window = d["ratio2060"].tail(B_CONVERGE_LOOKBACK)
    if not (conv_window.iloc[0] < conv_window.iloc[-1]):
        return None

    if not (today["Close"] > today["MA5"] > today["MA10"] > today["MA20"]):
        return None

    ma20_series = d["MA20"].tail(B_MA20_RISE_LOOKBACK + 1)
    if not (ma20_series.iloc[-1] > ma20_series.iloc[0]):
        return None

    if not (today["Close"] > today["MA20"]):
        return None

    return {
        "ratio_ma20_ma60": round(ratio_now, 4),
        "close": today["Close"], "ma5": round(today["MA5"], 1), "ma10": round(today["MA10"], 1),
        "ma20": round(today["MA20"], 1), "ma60": round(today["MA60"], 1),
        "last_bar": d.index[-1].date(),
    }


# ----------------------------------------------------------------------
# 메시지
# ----------------------------------------------------------------------
def esc(s) -> str:
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def fmt_num(n) -> str:
    try:
        return f"{round(n):,}"
    except Exception:
        return str(n)


def format_message(rows_a, rows_b, fetched, universe_size, bar_date) -> str:
    now = dt.datetime.now(KST)
    stale = bar_date is not None and bar_date != TODAY
    after_close = now.hour * 60 + now.minute >= MARKET_CLOSE_MIN

    head = [f"📈 <b>유목민 8일선 · 정배열 스캔</b> · {now.strftime('%m/%d (%a) %H:%M')}"]
    if stale:
        head.append(f"🔒 오늘 시세 없음(휴장 추정) — <b>{bar_date}</b> 종가 기준")
    elif after_close:
        head.append(f"✅ <b>{bar_date}</b> 종가 확정 기준")
    else:
        head.append("⏳ 장중 시세 기준 — 종가 확정 전 잠정 결과")
    head.append(f"대상 {universe_size}종목 중 {fetched}개 수집 · A {len(rows_a)}건 / B {len(rows_b)}건")
    head.append("")

    lines = list(head)

    lines.append("<b>━━━ 🟡 패턴 A · 5일선 이탈 → 8일선 지지 ━━━</b>")
    if rows_a:
        for r in rows_a[:TOP_N]:
            gap = r["gap_to_ma8_pct"]
            sign = "+" if gap > 0 else ""
            days = r["days_since_break"]
            when = "당일 이탈" if days == 0 else f"{days}일 전 이탈"
            lines.append(
                f"• <b>{esc(r['종목명'])}</b> <code>{r['종목코드']}</code> · {fmt_num(r['close'])}원\n"
                f"   8일선 {sign}{gap:.2f}% (MA8 {fmt_num(r['ma8'])}) · {when}"
            )
        if len(rows_a) > TOP_N:
            lines.append(f"   … 외 {len(rows_a) - TOP_N}건")
    else:
        lines.append("해당 종목 없음")
    lines.append("")

    lines.append("<b>━━━ 🟢 패턴 B · 역배열 → 정배열 전환 임박 ━━━</b>")
    if rows_b:
        for r in rows_b[:TOP_N]:
            ratio = r["ratio_ma20_ma60"]
            label = "골든크로스 완료" if ratio >= 1 else "전환 임박"
            lines.append(
                f"• <b>{esc(r['종목명'])}</b> <code>{r['종목코드']}</code> · {fmt_num(r['close'])}원\n"
                f"   MA20/MA60 {ratio:.3f} · {label}"
            )
        if len(rows_b) > TOP_N:
            lines.append(f"   … 외 {len(rows_b) - TOP_N}건")
    else:
        lines.append("해당 종목 없음")

    lines.append("")
    lines.append("<i>기술적 스크리닝 결과이며 투자 조언이 아닙니다.</i>")
    return "\n".join(lines)


# ----------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------
def main() -> int:
    t0 = time.time()
    deadline = t0 + MAX_FETCH_SEC

    try:
        print("[ma8] 유니버스 로딩...", file=sys.stderr)
        uni = get_universe(UNIVERSE_SIZE)
    except Exception as e:
        telegram.send(f"⚠️ <b>8일선 스캔 실패</b>\n유니버스 조회 오류: {esc(e)}")
        print(f"[ma8] 유니버스 실패: {e}", file=sys.stderr)
        return 1

    print(f"[ma8] {len(uni)}종목 수집 시작...", file=sys.stderr)
    price_data = fetch_all(uni["Code"].tolist(), deadline)
    print(f"[ma8] {len(price_data)}개 확보 ({time.time()-t0:.0f}초)", file=sys.stderr)

    if not price_data:
        telegram.send("⚠️ <b>8일선 스캔 실패</b>\n가격 데이터를 하나도 받지 못했습니다.")
        return 1

    name_map = dict(zip(uni["Code"], uni["Name"]))
    market_map = dict(zip(uni["Code"], uni["Market"]))

    rows_a, rows_b, bar_dates = [], [], []
    for code, df in price_data.items():
        df = add_mas(df)
        try:
            res_a = check_pattern_a(df)
        except Exception:
            res_a = None
        try:
            res_b = check_pattern_b(df)
        except Exception:
            res_b = None

        if res_a:
            rows_a.append({"종목코드": code, "종목명": name_map[code], "시장": market_map[code], **res_a})
        if res_b:
            rows_b.append({"종목코드": code, "종목명": name_map[code], "시장": market_map[code], **res_b})
        if len(df):
            bar_dates.append(df.index[-1].date())

    rows_a.sort(key=lambda r: abs(r["gap_to_ma8_pct"]))
    rows_b.sort(key=lambda r: r["ratio_ma20_ma60"])

    # 대다수 종목의 마지막 봉 날짜 = 시장 기준일 (휴장 판단용)
    bar_date = pd.Series(bar_dates).mode().iloc[0] if bar_dates else None

    msg = format_message(rows_a, rows_b, len(price_data), len(uni), bar_date)
    ok = telegram.send(msg)
    print(f"[ma8] A {len(rows_a)}건 / B {len(rows_b)}건 · 전송 {ok} · 총 {time.time()-t0:.0f}초", file=sys.stderr)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
