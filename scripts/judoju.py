"""월~금 09:30 / 12:00 / 15:00 (KST) — 당일 주도주 스냅샷.

데이터는 네이버 금융 '거래상위' 페이지 두 장이 전부다. 인증도 API 키도 없다.
    https://finance.naver.com/sise/sise_quant.naver?sosok=0  (코스피 약 80종목)
    https://finance.naver.com/sise/sise_quant.naver?sosok=1  (코스닥 약 97종목)

거래량 상위 목록이지만 거래대금·등락률·시가총액이 같은 표에 있어서,
받아온 뒤 거래대금 기준으로 다시 정렬하면 우리가 원하는 순위가 나온다.

09:30 은 알림을 보내지 않는다. '생존/신규' 판정의 기준선을 만드는 조용한 수집이다.

한계 — 어디까지나 '거래량' 상위 약 180종목이 모집단이다. 거래량은 적은데
거래대금만 큰 초고가주는 표에 안 잡힐 수 있다. 실측으로 삼성전자·SK하이닉스급은
문제없이 들어온다.
"""
import datetime as dt
import html as H
import json
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram, timeutil

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "judoju_state.json"
# 테마 맵은 주 1회만 바뀌고 덩치가 크다(130KB). 매일 바뀌는 스냅샷과 한 파일에
# 두면 커밋마다 통째로 다시 저장되어 저장소가 쓸데없이 커진다.
THEMES_PATH = ROOT / "data" / "judoju_themes.json"

URL = "https://finance.naver.com/sise/sise_quant.naver?sosok={sosok}"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

KEEP_DAYS = 10          # 상태 파일에 남길 거래일 수
STORE_RANK = 30         # 저장할 거래대금 순위. 출력이 아니라 저장이다.
HEADLINE = 5            # 리포트 상단에 크게 보여줄 최대 종목 수
HEADLINE_CHG = 3.0      # 🔥 로 올리는 최소 등락률(%)
MAX_CAP_EOK = 300_000   # 시가총액 30조(억 단위) 이상은 제외 — 삼성전자·하이닉스 편향 제거
MIN_PRICE = 1000        # 동전주 제외

# --- 눌림 관찰 -----------------------------------------------------------
# 급등한 날 사는 건 어렵다. 며칠 뒤 조정받을 때를 보자는 기능.
# '눌림'과 '무너짐'을 가르는 건 두 가지다 — 이동평균선을 지키는가, 거래량이 식었는가.
# 투매로 빠지면 거래량이 오히려 늘고, 쉬어가는 눌림이면 거래량이 준다.
PB_POOL = 80            # 눌림을 검사할 대상 (거래대금 상위 N종목)
PB_SURGE_CHG = 8.0      # 급등일 기준 — 전일 대비 %
PB_LOOKBACK = 11        # 급등일을 찾을 최근 거래일 수
PB_MIN_DROP = -5.0      # 급등 후 고점 대비 최소 하락률
PB_MAX_VOLR = 0.6       # 거래량이 급등일의 60% 이하로 식었을 것
PB_MA5_BAND = 4.0       # 5일선 ±4% 이내 (급등 1~3일 뒤 1차 눌림)
PB_MA8_LO, PB_MA8_HI = -3.0, 6.0   # 8일선 -3~+6% (급등 4~10일 뒤 2차 눌림)
# --- 테마 / 업종 ---------------------------------------------------------
# 첫 설계에서 "수동 매핑이 제일 어렵다"고 봤던 부분인데, 네이버 테마 페이지로 자동화된다.
# 266개 테마의 구성종목을 한 번 훑어 code→테마 맵을 만들고 일주일 캐시한다(약 70초).
# 테마가 없는 종목은 종목 페이지의 업종으로 대체한다.
THEME_LIST_URL = "https://finance.naver.com/sise/theme.naver?&page={page}"
THEME_DETAIL_URL = ("https://finance.naver.com/sise/"
                    "sise_group_detail.naver?type=theme&no={no}")
ITEM_URL = "https://finance.naver.com/item/main.naver?code={code}"
THEME_MAX_MEMBERS = 60  # 이보다 크면 '코스닥 우량주' 같이 뭉뚱그린 테마라 버린다
THEME_TTL_DAYS = 7      # 테마 맵을 다시 만드는 주기
LEAD_THEME_MIN = 3      # 상위 30 중 N종목 이상이면 '주도 테마'

DAILY_URL = ("https://api.finance.naver.com/siseJson.naver?symbol={code}"
             "&requestType=1&startTime={start}&endTime={end}&timeframe=day")
BAR_RE = re.compile(r"\[([^\[\]]+)\]")

# 슬롯별 유효 시간대 (KST, 분). 예약이 밀려 엉뚱한 시각에 돌면
# 잘못된 라벨로 상태 파일을 오염시키므로 아예 수집하지 않는다.
SLOT_WINDOW = {
    "0930": (9 * 60 + 15, 10 * 60 + 30),
    "1200": (11 * 60 + 30, 13 * 60 + 30),
    "1500": (14 * 60 + 30, 15 * 60 + 40),
}

# 이름으로 거르는 ETF·ETN·레버리지·인버스. 조건검색식 대신 쓰는 싸구려 필터지만
# 거래대금 상위를 어지럽히는 게 대부분 이쪽이라 효과는 크다.
NOISE = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "ACE ", "SOL ", "PLUS ", "RISE ",
         "HANARO", "KIWOOM", "TIMEFOLIO", "ETN", "레버리지", "인버스", "선물",
         "스팩", "제..호", "리츠")
PREF_RE = re.compile(r"\d*우(B|C)?$")          # 우선주
ROW_RE = re.compile(r"<tr>(.*?)</tr>", re.S)
LINK_RE = re.compile(r'code=(\d{6})" class="tltle">([^<]+)</a>')
NUM_RE = re.compile(r'<td class="number">(.*?)</td>', re.S)


# ---------------------------------------------------------------- 수집

def _clean(cell):
    return re.sub(r"<[^>]+>", "", cell).replace(",", "").replace("%", "").strip()


def is_noise(name):
    return PREF_RE.search(name) or any(k in name for k in NOISE)


def fetch_market(sosok):
    req = urllib.request.Request(URL.format(sosok=sosok), headers=UA)
    with urllib.request.urlopen(req, timeout=20) as r:
        page = r.read().decode("euc-kr", "replace")

    out = {}
    for tr in ROW_RE.findall(page):
        m = LINK_RE.search(tr)
        if not m:
            continue
        nums = [_clean(c) for c in NUM_RE.findall(tr)]
        if len(nums) < 8:
            continue
        try:
            # 현재가 / 전일비 / 등락률 / 거래량 / 거래대금(백만) / 매수 / 매도 / 시총(억)
            price, chg = int(nums[0]), float(nums[2])
            amt = int(nums[4]) * 1_000_000        # 백만원 → 원
            cap = int(nums[7]) if nums[7].isdigit() else 0
        except ValueError:
            continue
        out[m.group(1)] = (H.unescape(m.group(2)).strip(), price, chg, amt, cap)
    return out


def fetch_all():
    rows = {}
    for sosok in (0, 1):
        for attempt in range(3):
            try:
                rows.update(fetch_market(sosok))
                break
            except (urllib.error.URLError, OSError) as e:
                print(f"[judoju] sosok={sosok} 수집 실패({attempt + 1}/3): {e}",
                      file=sys.stderr)
                time.sleep(2)
        time.sleep(0.3)
    if not rows:
        raise RuntimeError("네이버에서 한 종목도 받지 못했습니다.")

    keep = []
    for code, (name, price, chg, amt, cap) in rows.items():
        if is_noise(name) or price < MIN_PRICE:
            continue
        if cap and cap >= MAX_CAP_EOK:
            continue
        keep.append([code, name, price, chg, amt, cap])
    keep.sort(key=lambda r: -r[4])

    print(f"[judoju] 원본 {len(rows)}종목 → 필터 후 {len(keep)}종목")
    snap = [[i, c, n, p, ch, a] for i, (c, n, p, ch, a, _) in
            enumerate(keep[:STORE_RANK], 1)]
    return snap, [r[:5] for r in keep[:PB_POOL]]


# ---------------------------------------------------------------- 상태

def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[judoju] 상태 파일이 깨져 새로 시작합니다: {e}", file=sys.stderr)
    return {"days": {}}


def save_state(state):
    for d in sorted(state["days"])[:-KEEP_DAYS]:
        del state["days"][d]
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")


def looks_like_holiday(state, ymd, snap):
    """휴장일이면 네이버가 전 거래일 수치를 그대로 보여준다.

    거래대금은 장중 1초마다 바뀌므로, 직전 거래일의 마지막 스냅샷과
    종목·거래대금이 완전히 같으면 장이 안 열린 것으로 본다.
    """
    prev = [d for d in sorted(state["days"]) if d < ymd]
    if not prev:
        return False
    day = state["days"][prev[-1]]
    for slot in ("1500", "1200", "0930"):
        if slot in day:
            before = [(r[1], r[5]) for r in day[slot]["snap"]]
            return before == [(r[1], r[5]) for r in snap]
    return False


# ---------------------------------------------------------------- 테마 / 업종

def _get(url, enc="euc-kr"):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        return r.read().decode(enc, "replace")


def build_theme_map():
    """code → [테마명] 맵을 통째로 만든다. 느리므로 일주일에 한 번만 부른다."""
    themes = {}
    for page in range(1, 10):
        try:
            html = _get(THEME_LIST_URL.format(page=page))
        except Exception:
            break
        found = dict(re.findall(r'type=theme&no=(\d+)["\'][^>]*>([^<]+)', html))
        fresh = {k: v for k, v in found.items() if k not in themes}
        if not fresh:
            break                       # 마지막 페이지를 넘기면 같은 내용이 반복된다
        themes.update(fresh)
        time.sleep(0.12)

    print(f"[judoju] 테마 {len(themes)}개 구성종목 수집 중... (1분쯤 걸립니다)")
    cmap = {}
    for no, raw_name in themes.items():
        try:
            html = _get(THEME_DETAIL_URL.format(no=no))
        except Exception:
            continue
        finally:
            time.sleep(0.12)
        codes = set(re.findall(r'code=(\d{6})["\'][^>]*>[^<]+</a>', html))
        if not codes or len(codes) > THEME_MAX_MEMBERS:
            continue                    # 너무 큰 테마는 정보량이 없다
        label = H.unescape(raw_name).strip()
        for c in codes:
            cmap.setdefault(c, []).append(label)
    print(f"[judoju] 테마 맵 완성 — {len(cmap)}종목")
    return cmap


def ensure_themes(state, ymd):
    """캐시된 테마 맵을 돌려주고, 일주일 지났으면 다시 만든다."""
    cached = {}
    if THEMES_PATH.exists():
        try:
            cached = json.loads(THEMES_PATH.read_text(encoding="utf-8"))
        except Exception:
            cached = {}
    built = cached.get("built", "")
    if built and cached.get("map"):
        age = (dt.datetime.strptime(ymd, "%Y%m%d")
               - dt.datetime.strptime(built, "%Y%m%d")).days
        if 0 <= age < THEME_TTL_DAYS:
            return cached["map"]
    try:
        cmap = build_theme_map()
    except Exception as e:
        print(f"[judoju] 테마 맵 생성 실패, 이전 것을 씁니다: {e}")
        return cached.get("map", {})
    if cmap:
        THEMES_PATH.write_text(
            json.dumps({"built": ymd, "map": cmap}, ensure_ascii=False,
                       sort_keys=True),
            encoding="utf-8")
    return cmap or cached.get("map", {})


def fetch_upjong(code):
    """테마가 없는 종목의 대체값. 종목 페이지는 EUC-KR 이 아니라 UTF-8 이다."""
    try:
        html = _get(ITEM_URL.format(code=code), enc="utf-8")
    except Exception:
        return ""
    m = re.search(r'type=upjong&no=\d+["\'][^>]*>([^<]+)', html)
    return H.unescape(m.group(1)).strip() if m else ""


def label_of(cmap, code, cache, hot=()):
    """표시용 섹터명. 오늘의 주도 테마에 속하면 그걸 우선 쓰고, 없으면 첫 테마, 그것도
    없으면 업종으로 대체한다. 같은 종목이 여러 테마에 속할 때 리포트가 따로 놀지 않게."""
    if code in cache:
        return cache[code]
    names = cmap.get(code) or []
    hot_names = {n for n, _ in hot}
    pick = next((n for n in names if n in hot_names), names[0] if names else "")
    if not pick:
        pick = fetch_upjong(code)
        time.sleep(0.12)
    cache[code] = short(pick)
    return cache[code]


def short(name):
    """'5G(5세대 이동통신)' → '5G'. 괄호 안 설명은 리포트에서 자리만 차지한다."""
    return re.split(r"[(\[]", name, 1)[0].strip() or name


def lead_themes(cmap, snap):
    """상위 30 안에서 같은 테마가 몇 개인지 — 종목이 아니라 테마가 주도하는 국면인지 본다."""
    count = {}
    for r in snap:
        for name in (cmap.get(r[1]) or [])[:3]:
            count[name] = count.get(name, 0) + 1
    hot = [(n, c) for n, c in count.items() if c >= LEAD_THEME_MIN]
    return sorted(hot, key=lambda x: -x[1])[:3]


# ---------------------------------------------------------------- 눌림 관찰

def fetch_daily(code, days=70):
    """네이버 일봉. [날짜, 시가, 고가, 저가, 종가, 거래량, 외국인소진율] 배열이 온다."""
    end = timeutil.now()
    url = DAILY_URL.format(code=code,
                           start=(end - dt.timedelta(days=days)).strftime("%Y%m%d"),
                           end=end.strftime("%Y%m%d"))
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=15) as r:
        txt = r.read().decode("cp949", "replace")

    bars = []
    for row in BAR_RE.findall(txt):
        c = [x.strip().strip("\'\"") for x in row.split(",")]
        if len(c) >= 6 and c[0].isdigit():
            try:
                bars.append({"d": c[0], "h": int(c[2]), "c": int(c[4]), "v": int(c[5])})
            except ValueError:
                pass
    return bars


def check_pullback(bars):
    """급등 후 조정 중이면 정보를 돌려주고, 아니면 None."""
    if len(bars) < 12:
        return None
    close = [b["c"] for b in bars]
    ma5 = sum(close[-5:]) / 5
    ma8 = sum(close[-8:]) / 8

    # 최근 며칠 안의 급등일. 오늘 급등은 눌림이 아니므로 어제까지만 본다.
    surge = None
    for i in range(max(1, len(bars) - PB_LOOKBACK), len(bars) - 1):
        if (bars[i]["c"] / bars[i - 1]["c"] - 1) * 100 >= PB_SURGE_CHG:
            surge = i
    if surge is None:
        return None

    high = max(b["h"] for b in bars[surge:])
    drop = (close[-1] / high - 1) * 100
    volr = bars[-1]["v"] / bars[surge]["v"] if bars[surge]["v"] else 9
    gap5 = (close[-1] / ma5 - 1) * 100
    gap8 = (close[-1] / ma8 - 1) * 100

    if drop > PB_MIN_DROP or volr > PB_MAX_VOLR:
        return None                       # 아직 안 눌렸거나, 거래량이 안 식었다(투매)
    if close[-1] <= bars[surge - 1]["c"]:
        return None                       # 급등분을 다 반납했다. 눌림이 아니라 되돌림.
    if abs(gap5) <= PB_MA5_BAND and close[-1] >= ma8:
        kind = "5일선"
    elif PB_MA8_LO <= gap8 <= PB_MA8_HI:
        kind = "8일선"
    else:
        return None                       # 지지선에서 멀거나 이미 무너졌다

    return {"kind": kind, "ago": len(bars) - 1 - surge, "drop": drop,
            "gap": gap5 if kind == "5일선" else gap8, "volr": volr,
            "surge_chg": (bars[surge]["c"] / bars[surge - 1]["c"] - 1) * 100}


def find_pullbacks(pool, state, ymd):
    """눌림 후보 목록. 오늘 거래대금 상위 + 최근 며칠 주도주였던 종목을 함께 본다."""
    seen, targets = set(), []
    for code, name, *_ in pool:
        seen.add(code)
        targets.append((code, name))
    for d in sorted(state["days"])[-5:]:                  # 눌리면 순위에서 사라지므로
        for slot in ("1200", "1500"):                      # 과거 주도주도 챙긴다
            for r in state["days"][d].get(slot, {}).get("snap", []):
                if r[1] not in seen and r[4] >= HEADLINE_CHG:
                    seen.add(r[1])
                    targets.append((r[1], r[2]))

    print(f"[judoju] 눌림 검사 {len(targets)}종목")
    hits = []
    for code, name in targets:
        try:
            info = check_pullback(fetch_daily(code))
        except Exception:
            continue
        finally:
            time.sleep(0.12)              # 네이버에 무리 가지 않게
        if info:
            hits.append({"code": code, "name": name, **info})
    print(f"[judoju] 눌림 후보 {len(hits)}개")
    return sorted(hits, key=lambda h: h["ago"])


# ---------------------------------------------------------------- 리포트

def fmt_amt(v):
    v = int(v or 0)
    if v >= 1_0000_0000_0000:
        return f"{v / 1_0000_0000_0000:.1f}조"
    if v >= 1_0000_0000:
        return f"{v / 1_0000_0000:,.0f}억"
    return f"{v:,}"


def esc(s):
    return H.escape(str(s), quote=False)


def streak_days(state, code, ymd):
    days = sorted(d for d in state["days"] if d <= ymd)[-5:]
    n = 0
    for d in days:
        if any(r[1] == code for slot in ("1200", "1500")
               for r in state["days"][d].get(slot, {}).get("snap", [])):
            n += 1
    return n


def build(state, ymd, slot, pullbacks=None, cmap=None):
    day = state["days"].get(ymd, {})
    cur = day.get(slot)
    if not cur:
        return None

    snap = cur["snap"]
    codes = {r[1] for r in snap}
    morning = {r[1] for r in day.get("0930", {}).get("snap", [])}

    head = [r for r in snap if r[4] >= HEADLINE_CHG][:HEADLINE]
    head_codes = {r[1] for r in head}
    survived = [r for r in snap if r[1] in morning and r[1] not in head_codes]
    fresh = [r for r in snap if r[1] not in morning and r[1] not in head_codes]
    dropped = morning - codes

    cmap = cmap or {}
    seccache = {}

    label = {"0930": "09:30", "1200": "12:00", "1500": "15:00"}[slot]
    lines = [f"📊 <b>{label} 주도주</b> · {timeutil.stamp('%m/%d (%a)')}", ""]

    hot = lead_themes(cmap, snap)
    if hot:
        lines.append("🏷 주도 테마 — "
                     + " · ".join(f"{esc(short(n))} {c}" for n, c in hot))
        lines.append("")

    if head:
        for rank, code, name, price, chg, amt in head:
            st = streak_days(state, code, ymd)
            # 09:30 기준선이 없는 날에는 '신규'를 붙이지 않는다 (전부 신규가 되어버린다)
            tag = (f"[{st}일]" if st >= 2
                   else "[신규]" if morning and code not in morning else "")
            sec = label_of(cmap, code, seccache, hot)
            sec = f" · {esc(sec)}" if sec else ""
            lines.append(f"🔥 <b>{esc(name)}</b>{sec}  {chg:+.1f}%  "
                         f"{fmt_amt(amt)}  {rank}위 {tag}".rstrip())
    else:
        lines.append(f"🔥 거래대금 상위 중 +{HEADLINE_CHG:.0f}% 이상 없음")
        lines.append("   (오늘은 방향이 안 잡힌 장)")

    lines.append("")
    if morning:
        if survived:
            lines.append("생존 %d: %s" % (
                len(survived), " / ".join(esc(r[2]) for r in survived[:6])))
        if fresh:
            lines.append("신규 %d: %s" % (
                len(fresh), " / ".join(esc(r[2]) for r in fresh[:6])))
        if dropped:
            lines.append(f"이탈 {len(dropped)}")
    else:
        lines.append("<i>09:30 기준선이 없어 생존·신규 비교는 생략합니다.</i>")

    if pullbacks:
        lines += ["", "🎯 <b>눌림 관찰</b> — 급등 후 조정 중"]
        for h in pullbacks[:5]:
            sec = label_of(cmap, h["code"], seccache, hot)
            sec = f" · {esc(sec)}" if sec else ""
            lines.append(
                f"   {esc(h['name'])}{sec}  {h['ago']}일전 {h['surge_chg']:+.0f}%  "
                f"고점 {h['drop']:+.0f}%  {h['kind'][0]}선 {h['gap']:+.1f}%  "
                f"거래량 {h['volr']:.0%}")
        lines.append("   <i>매수 신호가 아니라 관찰 후보입니다.</i>")

    return "\n".join(lines)


# ---------------------------------------------------------------- 메인

def main() -> int:
    import os
    slot = (os.environ.get("JUDOJU_SLOT") or "").strip()
    now = timeutil.now()
    if slot not in SLOT_WINDOW:
        cur = now.hour * 60 + now.minute
        slot = "0930" if cur < 11 * 60 else "1200" if cur < 14 * 60 else "1500"
        print(f"[judoju] JUDOJU_SLOT 미지정 → 시각으로 {slot} 추정")

    ymd = now.strftime("%Y%m%d")
    print(f"[judoju] {now:%Y-%m-%d %H:%M:%S} KST  슬롯 {slot}")

    if now.weekday() >= 5:
        print("[judoju] 주말이라 건너뜁니다.")
        return 0

    lo, hi = SLOT_WINDOW[slot]
    cur = now.hour * 60 + now.minute
    if not (lo <= cur <= hi) and os.environ.get("JUDOJU_FORCE") != "1":
        print(f"[judoju] 슬롯 {slot} 의 유효 시간대"
              f"({lo // 60:02d}:{lo % 60:02d}~{hi // 60:02d}:{hi % 60:02d})를 "
              f"벗어났습니다. 예약이 밀린 것으로 보고 건너뜁니다. "
              f"(강제 실행은 JUDOJU_FORCE=1)")
        return 0

    state = load_state()
    try:
        snap, pool = fetch_all()
    except Exception as e:
        print(f"[judoju] 수집 실패: {e}", file=sys.stderr)
        return 1

    if looks_like_holiday(state, ymd, snap):
        print("[judoju] 직전 거래일과 수치가 동일합니다. 휴장일로 보고 건너뜁니다.")
        return 0

    state["days"].setdefault(ymd, {})[slot] = {
        "snap": snap, "at": now.strftime("%H:%M:%S")}
    save_state(state)

    if slot == "0930":
        print(f"[judoju] 09:30 기준선 {len(snap)}종목 저장 (알림 없음).")
        return 0

    # 눌림 검사는 종목별 일봉을 받아야 해서 20초쯤 걸린다. 알림 슬롯에서만 한다.
    cmap = ensure_themes(state, ymd)
    text = build(state, ymd, slot, find_pullbacks(pool, state, ymd), cmap)
    if not text:
        print("[judoju] 리포트를 만들 데이터가 없습니다.", file=sys.stderr)
        return 1
    print(text)
    return 0 if telegram.send(text) else 1


if __name__ == "__main__":
    sys.exit(main())
