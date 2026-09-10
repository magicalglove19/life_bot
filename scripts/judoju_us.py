#!/usr/bin/env python3
"""월~금 미국장 마감 후 (KST 아침) — 미국 주도주 스냅샷.

한국판(scripts/judoju.py)의 미국 이식. 데이터는 두 곳에서만 온다. 인증도 API 키도 없다.

    나스닥 스크리너  https://api.nasdaq.com/api/screener/stocks?download=true
        미국 전 상장사 약 7,100종목. 현재가·등락률·거래량·시가총액이 한 번에 온다.
        ETF·ETN 은 분류 단계에서 아예 빠져 있어서, 한국판이 KODEX·TIGER 를
        이름으로 거르던 작업이 필요 없다. NVDL·TSLL 같은 한 종목 레버리지도
        같은 이유로 안 들어온다(실측 0개). 그래도 is_leveraged() 로 한 번 더 막는다.
    야후 일봉      https://query1.finance.yahoo.com/v8/finance/chart/{sym}
        RVOL·눌림 계산용. 세션 날짜의 근거이기도 하다.

한국판과 갈라진 세 가지 — 미국 시장이라서 어쩔 수 없다
    1) 슬롯(0930/1200/1500)이 없다. 미국 장중은 KST 23:00~06:00 이라 사람이 잔다.
       '완결된 세션 = 스냅샷 1개'로 바꾸고, 09:30 기준선이 하던 생존·신규 판정을
       전 거래일 대비로 옮겼다.
    2) 시총 $200B 로 두 버킷을 만든다. 한국판은 30조 이상을 버렸지만 미국에서
       그러면 INTC +9% 를 버리게 된다. 버리는 대신 따로 세운다.
    3) RVOL 을 같이 본다. 7,100종목 시장에서 절대 거래대금만으로는
       "원래 큰 놈"과 "오늘 돈이 몰린 놈"이 안 갈린다.

⏰ 한국판과 달리 **예약 지연을 걱정할 필요가 없다.** 한국판은 12:00 스냅샷이
   12:00 에 찍혀야 해서 repository_dispatch 가 필요했다. 이쪽은 '미국장 마감 후,
   다음 개장 전' 아무 때나 돌면 되고 그 창이 17시간 반이다. 예약이 4시간 밀려도
   멀쩡하다. 장중에 돌면 저장하지 않고 건너뛴다(미완결 세션이 다음날 비교를 오염).
"""

import html as H
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram

ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "data" / "judoju_us_state.json"

ET = ZoneInfo("America/New_York")
KST = ZoneInfo("Asia/Seoul")

SCREENER = ("https://api.nasdaq.com/api/screener/stocks"
            "?tableonly=true&limit=0&offset=0&download=true")
CHART = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
         "?range=4mo&interval=1d")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
      "Accept": "application/json, text/plain, */*"}
REF_SYMBOL = "NVDA"     # 세션 날짜를 물어볼 기준 종목. 매일 반드시 거래된다.

KEEP_DAYS = 15          # 상태 파일에 남길 거래일 수
STORE_RANK = 30         # 버킷별로 저장할 순위. 출력이 아니라 저장이다.
HEADLINE = 4            # 버킷별로 리포트 상단에 올릴 최대 종목 수
HEADLINE_CHG = 3.0      # 🔥 로 올리는 최소 등락률(%)
MEGA_CAP = 200e9        # 이 위는 '대형', 아래는 '중소형'. 실측으로 정한 값 —
                        # $200B 아래로 자르면 거래대금 상위20 중 4~5개만 남고
                        # 그게 실제로 매일 바뀌는 이름들이다.
MIN_PRICE = 5.0         # 동전주 제외
MIN_VOLUME = 200_000    # 유동성 하한
MIN_DOLLAR_VOL = 50e6   # 거래대금 $50M 미만은 주도주 논의 대상이 아니다

# 나스닥 스크리너에 ETF 는 없지만 워런트·우선주·유닛·권리·스팩은 들어 있다.
# 심볼 규칙(^, 접미사)보다 이름에 그대로 적혀 있는 쪽이 정확하다.
NOISE = ("Warrant", "Unit", "Preferred", "Right", "Depositary Share",
         "Notes", "Debenture", "Acquisition Corp", "Trust Units")

# --- 레버리지·파생 상품 차단 --------------------------------------------
# NVDL·TSLL·MSTU 같은 '한 종목 2배' 상품은 주도주가 아니라 주도주의 그림자다.
# 실측(2026-09-09): 나스닥 screener/stocks 는 이걸 분류 단계에서 이미 빼서
# 7,128종목 중 0개다. 그래도 막아두는 건 분류가 바뀌면 조용히 새기 때문이다.
#
# 단어 하나로 거르면 안 된다. 같은 날 실측에서 'Ultra'·'Bull'·'Bear'·'2X' 를
# 단어로 걸면 Ultragenyx(RARE), Ultra Clean(UCTT), BigBear.ai(BBAI),
# Bullish(BLSH), RBC Bearings, V2X 가 전부 죽는다 — 전부 멀쩡한 기업이고
# 그중엔 주도주 후보도 있다. 그래서 발행사 이름과 구조를 나타내는 '구'로만 잡는다.
LEV_ISSUERS = ("Direxion", "ProShares", "GraniteShares", "T-Rex ", "Tradr ",
               "Defiance ", "Leverage Shares", "YieldMax", "Roundhill",
               "Volatility Shares", "Kurv ", "REX-", "Innovator ",
               "AXS ", "Simplify ")
# 'MAX ' 는 발행사지만 못 쓴다 — Real REMAX Group(REAX)·XMAX 를 같이 죽인다.
# 이 발행사 상품은 이름이 전부 ETF/ETN 으로 끝나서 아래 구(句)에서 걸린다.
LEV_PHRASES = (" ETF", " ETN", "Exchange Traded Fund", "Exchange-Traded Note",
               "Exchange Traded Note", "1.5X Long", "1.5X Short", "2X Long",
               "2X Short", "3X Long", "3X Short", "1X Short", "-1X ",
               "Bull 2X", "Bull 3X", "Bear 1X", "Bear 2X", "Bear 3X",
               "Daily Target", "Leveraged Long", "Leveraged Short",
               "Ultra Long", "Ultra Short", "UltraPro", "Inverse ",
               "Covered Call", "Option Income", "Income Strategy",
               "Index Fund", "Trust Series", "Linked to the")


def is_leveraged(name):
    return any(k in name for k in LEV_ISSUERS) or any(k in name for k in LEV_PHRASES)

# --- 눌림 관찰 -----------------------------------------------------------
# 한국판과 같은 논리다. '눌림'과 '무너짐'을 가르는 건 두 가지 —
# 이동평균선을 지키는가, 거래량이 식었는가.
PB_POOL = 60            # 눌림·RVOL 을 검사할 대상 (버킷 합쳐 거래대금 상위 N)
PB_SURGE_CHG = 8.0      # 급등일 기준 — 전일 대비 %. 미국에서 하루 +8% 는 여전히 큰 사건이다
PB_LOOKBACK = 11        # 급등일을 찾을 최근 거래일 수
PB_MIN_DROP = -5.0      # 급등 후 고점 대비 최소 하락률
PB_MAX_VOLR = 0.6       # 거래량이 급등일의 60% 이하로 식었을 것
PB_MA5_BAND = 4.0       # 5일선 ±4% 이내 (급등 1~3일 뒤 1차 눌림)
PB_MA8_LO, PB_MA8_HI = -3.0, 6.0   # 8일선 -3~+6% (급등 4~10일 뒤 2차 눌림)
RVOL_DAYS = 20          # RVOL 기준 기간


# --- 업종 이름 --------------------------------------------------------------
# 나스닥은 sector 와 industry 를 둘 다 준다. 리포트에 쓰는 건 industry 다.
# sector 는 거래대금 상위 12개 중 9개가 'Technology' 로 뭉쳐서(실측 2026-09-10)
# NVDA(반도체)와 META(소프트웨어)를 구분해 주지 못한다. 주도주 리포트에서
# 알고 싶은 건 '돈이 어느 업종으로 갔나' 이므로 갈라주는 쪽을 쓴다.
# 이름이 길고 나스닥 특유의 표기라 짧은 한글로 바꾼다 — 상위 100종목 기준
# 적중률 99%. 표에 없으면 영문을 다듬어 그대로 쓴다.
INDUSTRY_KO = {
    "Semiconductors": "반도체",
    "Computer Software: Prepackaged Software": "SW",
    "Computer Software: Programming Data Processing": "SW·데이터",
    "EDP Services": "IT서비스",
    "Computer Manufacturing": "컴퓨터",
    "Computer peripheral equipment": "컴퓨터주변",
    "Computer Communications Equipment": "네트워크장비",
    "Electronic Components": "전자부품",
    "Telecommunications Equipment": "통신장비",
    "Radio And Television Broadcasting And Communications Equipment": "방송통신장비",
    "Consumer Electronics/Appliances": "가전",
    "Industrial Machinery/Components": "산업기계",
    "Metal Fabrications": "금속가공",
    "Construction/Ag Equipment/Trucks": "건설기계",
    "Auto Manufacturing": "자동차",
    "Aerospace": "항공우주",
    "Military/Government/Technical": "방산",
    "Biotechnology: Pharmaceutical Preparations": "제약",
    "Biotechnology: Biological Products (No Diagnostic Substances)": "바이오",
    "Biotechnology: In Vitro & In Vivo Diagnostic Substances": "진단",
    "Biotechnology: Laboratory Analytical Instruments": "분석장비",
    "Medical/Dental Instruments": "의료기기",
    "Medical Specialities": "의료서비스",
    "Major Pharmaceuticals": "대형제약",
    "Major Banks": "은행",
    "Savings Institutions": "저축은행",
    "Investment Bankers/Brokers/Service": "증권",
    "Investment Managers": "자산운용",
    "Finance: Consumer Services": "소비자금융",
    "Property-Casualty Insurers": "손해보험",
    "Life Insurance": "생명보험",
    "Real Estate Investment Trusts": "리츠",
    "Real Estate": "부동산",
    "Homebuilding": "주택건설",
    "Integrated oil Companies": "정유",
    "Oil & Gas Production": "석유가스",
    "Oilfield Services/Equipment": "오일서비스",
    "Natural Gas Distribution": "가스",
    "Coal Mining": "석탄",
    "Electric Utilities: Central": "전력",
    "Power Generation": "발전",
    "Major Chemicals": "화학",
    "Specialty Chemicals": "정밀화학",
    "Precious Metals": "귀금속",
    "Steel/Iron Ore": "철강",
    "Metal Mining": "광산",
    "Business Services": "기업서비스",
    "Diversified Commercial Services": "상업서비스",
    "Professional Services": "전문서비스",
    "Advertising": "광고",
    "Catalog/Specialty Distribution": "이커머스",
    "Department/Specialty Retail Stores": "유통",
    "Clothing/Shoe/Accessory Stores": "의류유통",
    "RETAIL: Building Materials": "건자재유통",
    "Retail: Computer Software & Peripheral Equipment": "IT유통",
    "Consumer Electronics/Video Chains": "가전유통",
    "Retail-Auto Dealers and Gas Stations": "자동차유통",
    "Food Chains": "식품유통",
    "Restaurants": "외식",
    "Beverages (Production/Distribution)": "음료",
    "Package Goods/Cosmetics": "화장품",
    "Apparel": "의류",
    "Shoe Manufacturing": "신발",
    "Farming/Seeds/Milling": "농업",
    "Transportation Services": "운송",
    "Air Freight/Delivery Services": "항공화물",
    "Marine Transportation": "해운",
    "Railroads": "철도",
    "Trucking Freight/Courier Services": "화물운송",
    "Hotels/Resorts": "호텔",
    "Casinos": "카지노",
    "Movies/Entertainment": "엔터",
    "Cable & Other Pay Television Services": "케이블TV",
    "Telecommunications": "통신",
    "Building Products": "건자재",
    "Engineering & Construction": "건설",
    "Containers/Packaging": "포장재",
    "Ordnance And Accessories": "무기",
    "Blank Checks": "스팩",
}


def industry_ko(ind, sector=""):
    """나스닥 업종명을 짧은 한글로. 표에 없으면 영문을 다듬어 쓴다."""
    if not ind:
        return sector or ""
    if ind in INDUSTRY_KO:
        return INDUSTRY_KO[ind]
    s = ind.split(":")[-1].split("/")[0].split("(")[0].strip()
    return s[:14] or sector or "기타"

# ---------------------------------------------------------------- 수집

def _num(v):
    """'$146.85' '-2.658%' '1,603,231' → float. 빈칸·N/A 는 0."""
    s = re.sub(r"[$,%\s]", "", str(v or ""))
    try:
        return float(s)
    except ValueError:
        return 0.0


def is_noise(name, symbol):
    return ("^" in symbol or is_leveraged(name)
            or any(k in name for k in NOISE))


def get_json(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def fetch_screener():
    """전 종목을 받아 잡음을 걸러내고 거래대금 순으로 세운다."""
    last = None
    for attempt in range(3):
        try:
            rows = get_json(SCREENER)["data"]["rows"]
            break
        except Exception as e:                       # 네트워크·JSON 양쪽 다
            last = e
            print(f"  수집 실패({attempt + 1}/3): {e}")
            time.sleep(3)
    else:
        raise RuntimeError(f"나스닥 스크리너에서 받지 못했습니다: {last}")

    keep = []
    for r in rows:
        sym, name = r.get("symbol", ""), r.get("name", "")
        price, vol = _num(r.get("lastsale")), _num(r.get("volume"))
        if is_noise(name, sym) or price < MIN_PRICE or vol < MIN_VOLUME:
            continue
        dv = price * vol
        if dv < MIN_DOLLAR_VOL:
            continue
        keep.append([sym, short_name(name), price, _num(r.get("pctchange")),
                     dv, _num(r.get("marketCap")),
                     industry_ko(r.get("industry"), r.get("sector"))])
    keep.sort(key=lambda x: -x[4])
    print(f"  원본 {len(rows)}종목 → 필터 후 {len(keep)}종목")
    return keep


# 긴 것부터 지워야 한다. " Common Stock" 을 먼저 지우면
# "Bloom Energy Corporation Class A Common Stock" 이 "... Class A" 로 남는다.
_NAME_TAILS = (" Class A Common Stock", " Class B Common Stock",
               " Class C Common Stock", " Class A Ordinary Shares",
               " Class C Capital Stock", " American Depositary Shares",
               " Common Stock", " Common Shares", " Ordinary Shares",
               " Capital Stock", " Class A", " Class B", " Class C")
_CORP_TAILS = (" Corporation", " Incorporated", " Holdings", " Company",
               " Limited", " Group", " Inc.", " Inc", " Ltd.", " Ltd",
               " plc", " PLC", " N.V.", " S.A.", " Co.", " Corp.", " Corp")


def short_name(name):
    """'NVIDIA Corporation Common Stock' → 'NVIDIA'. 리포트 폭을 지키려는 것."""
    name = name.strip().rstrip(",")
    for _ in range(3):                       # 꼬리가 겹쳐 붙은 경우가 있다
        for tail in _NAME_TAILS + _CORP_TAILS:
            if name.endswith(tail):
                name = name[: -len(tail)].strip(" ,.")
                break
        else:
            break
    return name


def split_buckets(rows):
    """대형/중소형으로 가른다. 시총이 0(데이터 없음)이면 중소형으로 본다."""
    mega = [r for r in rows if r[5] >= MEGA_CAP][:STORE_RANK]
    small = [r for r in rows if r[5] < MEGA_CAP][:STORE_RANK]
    return ([[i, *r] for i, r in enumerate(mega, 1)],
            [[i, *r] for i, r in enumerate(small, 1)])


# ---------------------------------------------------------------- 일봉 / 세션

def _meta_day(meta):
    """meta.regularMarketTime 이 가리키는 정규장 날짜(ET)."""
    ts = meta.get("regularMarketTime")
    return datetime.fromtimestamp(ts, ET) if ts else None


def fetch_daily(sym):
    """야후 일봉. [{d, h, c, v}] 로 정리해서 돌려준다.

    ⚠️ 마지막 봉을 meta 로 메운다. 야후는 **마감 직후 몇 시간 동안 그날 일봉의
    close 를 채우지 않는다** — 실측(2026-09-09 20:40 ET, 마감 4시간 40분 뒤)에도
    close=None 이었고 high·volume 만 들어 있었다. 그런데 같은 응답의 meta 에는
    확정된 종가(regularMarketPrice)·고가·거래량이 이미 들어 있다.

    이걸 안 메우면 '마지막 봉 = 어제'가 되어 세션이 하루씩 밀린다. 그러면
    오늘 마감 데이터가 어제 이름으로 저장되고, 그 뒤로 계속 밀려서 전 거래일
    대비 비교가 통째로 무너진다.
    """
    d = get_json(CHART.format(sym=urllib.parse.quote(sym.replace("/", "-"))),
                 timeout=20)["chart"]["result"][0]
    q, meta = d["indicators"]["quote"][0], d["meta"]
    rmt = _meta_day(meta)
    last = len(d["timestamp"]) - 1

    bars = []
    for i, ts in enumerate(d["timestamp"]):
        day = datetime.fromtimestamp(ts, ET).strftime("%Y%m%d")
        c, h, v = q["close"][i], q["high"][i], q["volume"][i]
        if i == last and rmt and rmt.strftime("%Y%m%d") == day:
            if c is None:
                c = meta.get("regularMarketPrice")
            if h is None:
                h = meta.get("regularMarketDayHigh")
            if v is None:
                v = meta.get("regularMarketVolume")
        if c is None or h is None or v is None:
            continue                                  # 거래정지일 등 구멍
        bars.append({"d": day, "h": float(h), "c": float(c), "v": int(v)})
    return bars, meta


def session_info():
    """(세션날짜 YYYYMMDD, 그 세션이 끝났는가).

    시계로 추정하지 않는다 — 그러면 미국 공휴일 표를 코드에 박아야 한다.
    야후 meta 의 regularMarketTime 이 '마지막 정규장이 언제 끝났는가'를
    직접 알려주므로 Labor Day 든 Thanksgiving 이든 저절로 맞는다.
    (실측: 2026-09-07 Labor Day 는 봉에도 meta 에도 없어 09-04 → 09-08 로 건너뛴다)

    봉이 아니라 meta 를 보는 이유는 fetch_daily 의 주석에 있다 — 마감 직후
    몇 시간 동안 그날 봉의 close 가 비어 있어서, 봉만 보면 하루씩 밀린다.
    """
    bars, meta = fetch_daily(REF_SYMBOL)
    if not bars:
        raise RuntimeError(f"{REF_SYMBOL} 일봉을 받지 못했습니다.")
    rmt = _meta_day(meta)
    if rmt is None:                                   # meta 가 없으면 봉으로 물러선다
        ymd, now_et = bars[-1]["d"], datetime.now(ET)
        return ymd, not (ymd == now_et.strftime("%Y%m%d")
                         and now_et.hour * 60 + now_et.minute < 16 * 60)

    ymd = rmt.strftime("%Y%m%d")
    now_et = datetime.now(ET)
    # 그 세션이 끝났나 — 날짜가 오늘 이전이면 당연히 끝났고,
    # 오늘이면 16:00 ET 를 지났는지로 가른다.
    closed = (rmt.date() < now_et.date()
              or now_et.hour * 60 + now_et.minute >= 16 * 60)
    return ymd, closed


# ---------------------------------------------------------------- 상태

def load_state():
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  상태 파일이 깨져 새로 시작합니다: {e}")
    return {"days": {}}


def save_state(state):
    for d in sorted(state["days"])[:-KEEP_DAYS]:
        del state["days"][d]
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8")


def prev_day(state, ymd):
    p = [d for d in sorted(state["days"]) if d < ymd]
    return state["days"][p[-1]] if p else None


def all_codes(day):
    if not day:
        return set()
    return {r[1] for b in ("mega", "small") for r in day.get(b, [])}


def streak_days(state, sym, ymd):
    """오늘 포함해 며칠 연속으로 상위에 있었나."""
    days = sorted(d for d in state["days"] if d <= ymd)
    n = 0
    for d in reversed(days):
        if sym in all_codes(state["days"][d]):
            n += 1
        else:
            break
    return n


# ---------------------------------------------------------------- 눌림 / RVOL

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
        if close[i - 1] and (close[i] / close[i - 1] - 1) * 100 >= PB_SURGE_CHG:
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
    if close[-1] <= close[surge - 1]:
        return None                       # 급등분을 다 반납했다. 눌림이 아니라 되돌림.
    if abs(gap5) <= PB_MA5_BAND and close[-1] >= ma8:
        kind = "5일선"
    elif PB_MA8_LO <= gap8 <= PB_MA8_HI:
        kind = "8일선"
    else:
        return None                       # 지지선에서 멀거나 이미 무너졌다

    return {"kind": kind, "ago": len(bars) - 1 - surge, "drop": drop,
            "gap": gap5 if kind == "5일선" else gap8, "volr": volr,
            "surge_chg": (close[surge] / close[surge - 1] - 1) * 100}


def rvol(bars):
    """오늘 거래대금이 최근 RVOL_DAYS 중앙값의 몇 배인가.

    평균이 아니라 중앙값을 쓴다. 급등 하루가 평균을 끌어올려서
    '평소'가 평소가 아니게 되는 걸 막으려는 것.
    """
    if len(bars) < RVOL_DAYS + 1:
        return None
    past = sorted(b["c"] * b["v"] for b in bars[-RVOL_DAYS - 1:-1])
    med = past[len(past) // 2]
    if not med:
        return None
    return bars[-1]["c"] * bars[-1]["v"] / med


def scan_pool(pool, state, ymd):
    """상위 종목의 일봉을 한 번씩 받아 RVOL 과 눌림을 같이 계산한다.

    두 기능이 같은 데이터를 쓰므로 요청은 한 번만 한다.
    눌리면 순위에서 사라지기 때문에, 최근 며칠 주도주였던 종목도 함께 본다.
    """
    seen, targets = set(), []
    for r in pool[:PB_POOL]:          # [순위, 심볼, 이름, ...]
        seen.add(r[1])
        targets.append((r[1], r[2]))
    for d in sorted(state["days"])[-5:]:
        for b in ("mega", "small"):
            for r in state["days"][d].get(b, []):
                if r[1] not in seen and r[4] >= HEADLINE_CHG:
                    seen.add(r[1])
                    targets.append((r[1], r[2]))

    print(f"  일봉 조회 {len(targets)}종목...", end="", flush=True)
    rv, hits, fail = {}, [], 0
    for sym, name in targets:
        try:
            bars, _ = fetch_daily(sym)
            r = rvol(bars)
            if r:
                rv[sym] = r
            info = check_pullback(bars)
            if info:
                hits.append({"sym": sym, "name": name, **info})
        except Exception:
            fail += 1
        time.sleep(0.15)                  # 야후에 무리 가지 않게
    print(f" RVOL {len(rv)}개 / 눌림 {len(hits)}개" + (f" / 실패 {fail}" if fail else ""))
    return rv, sorted(hits, key=lambda h: h["ago"])


# ---------------------------------------------------------------- 리포트

def fmt_usd(v):
    v = float(v or 0)
    if v >= 1e9:
        return f"${v / 1e9:.1f}B"
    if v >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def esc(s):
    return H.escape(str(s), quote=False)


def tag_for(state, sym, ymd, prev):
    st = streak_days(state, sym, ymd)
    if st >= 2:
        return f"[{st}일]"
    return "[신규]" if prev and sym not in all_codes(prev) else ""


def bucket_lines(state, ymd, rows, prev, rv, title):
    head = [r for r in rows if r[4] >= HEADLINE_CHG][:HEADLINE]
    if not head:
        return [f"🔥 <b>{title}</b> — +{HEADLINE_CHG:.0f}% 이상 없음"]
    out = [f"🔥 <b>{title}</b>"]
    for rank, sym, name, price, chg, dv, cap, *rest in head:
        r = rv.get(sym)
        rtxt = f" RVOL {r:.1f}x" if r else ""
        ind = rest[0] if rest else ""
        out.append(f"  <b>{esc(sym)}</b> {chg:+.1f}% {fmt_usd(dv)}{rtxt} "
                   f"{rank}위 {tag_for(state, sym, ymd, prev)} "
                   f"<i>{esc(ind)}{' · ' if ind else ''}{esc(name[:18])}</i>"
                   .replace("  ", " ").rstrip())
    return out


def industry_tally(day, top=6):
    """저장된 스냅샷 전체(대형+중소형)를 업종별로 센다.

    상위 몇 종목이 아니라 저장분 전부를 센다 — 리포트에 이름이 오르지 못한
    5위 밖에도 같은 업종이 깔려 있으면 그게 오늘 장의 성격이다.
    """
    c = {}
    for b in ("mega", "small"):
        for r in day.get(b, []):
            ind = r[7] if len(r) > 7 else ""
            if ind:
                c[ind] = c.get(ind, 0) + 1
    return [kv for kv in sorted(c.items(), key=lambda kv: -kv[1]) if kv[1] >= 2][:top]


def build(state, ymd, rv=None, pullbacks=None):
    day = state["days"].get(ymd)
    if not day:
        return None
    rv = rv or {}
    mega, small = day.get("mega", []), day.get("small", [])
    prev = prev_day(state, ymd)

    d = datetime.strptime(ymd, "%Y%m%d")
    wd = "월화수목금토일"[d.weekday()]
    lines = [f"🇺🇸 <b>미국 주도주</b> · {d:%m/%d}({wd}) 마감", ""]

    lines += bucket_lines(state, ymd, small, prev, rv, "중소형 (시총 $200B 미만)")
    lines.append("")
    lines += bucket_lines(state, ymd, mega, prev, rv, "대형 (시총 $200B 이상)")
    lines.append("")

    if prev:
        cur, old = all_codes(day), all_codes(prev)
        stay = [r for b in ("mega", "small") for r in day[b] if r[1] in old]
        new = [r for b in ("mega", "small") for r in day[b] if r[1] not in old]
        lines.append(f"생존 {len(stay)} / 신규 {len(new)} / 이탈 {len(old - cur)}")
        if new:
            lines.append("신규: " + " ".join(esc(r[1]) for r in
                                            sorted(new, key=lambda r: -r[5])[:8]))
    else:
        lines.append("<i>전 거래일 기록이 없어 생존·신규 비교는 생략합니다.</i>")

    tally = industry_tally(day)
    if tally:
        lines.append("업종: " + " · ".join(f"{esc(k)} {v}" for k, v in tally))

    if pullbacks:
        lines += ["", "🎯 <b>눌림 관찰</b> — 급등 후 조정 중"]
        for h in pullbacks[:5]:
            lines.append(
                f"   {esc(h['sym'])}  {h['ago']}일전 {h['surge_chg']:+.0f}%  "
                f"고점 {h['drop']:+.0f}%  {h['kind'][0]}선 {h['gap']:+.1f}%  "
                f"거래량 {h['volr']:.0%}")
        lines.append("   <i>매수 신호가 아니라 관찰 후보입니다.</i>")

    return "\n".join(lines)


def plain(text):
    return re.sub(r"</?[bi]>", "", text)


# ---------------------------------------------------------------- 메인

def preview(mega, small, live):
    when = "장중" if live else "마감"
    print(f"\n  📊 지금 거래대금 상위 ({when} 기준, 저장 안 함)\n")
    for title, rows in (("중소형", small), ("대형", mega)):
        print(f"   ── {title}")
        for rank, sym, name, price, chg, dv, cap, *rest in rows[:8]:
            mark = "🔥" if chg >= HEADLINE_CHG else "  "
            ind = rest[0] if rest else ""
            print(f"   {mark} {rank:2d}. {sym:6s} {chg:+6.1f}%  "
                  f"{fmt_usd(dv):>8s}  {ind[:8]:10s} {name[:22]}")
        print()
    if live:
        print("  (미국 장이 열려 있습니다. 마감 후 다시 실행하면 저장합니다.)")


def main() -> int:
    import os
    args = list(sys.argv[1:])
    for env, flag in (("JUDOJU_US_FORCE", "--force"),
                      ("JUDOJU_US_QUICK", "--quick"),
                      ("JUDOJU_US_DRYRUN", "--dry-run")):
        if os.environ.get(env) == "1":
            args.append(flag)
    now_kst, now_et = datetime.now(KST), datetime.now(ET)

    if "--test" in args:
        return 0 if telegram.send("✅ 미국 주도주 봇 테스트 메시지입니다.") else 1

    print(f"\n=== KST {now_kst:%Y-%m-%d %H:%M}  |  ET {now_et:%m-%d %H:%M %Z} ===")
    try:
        ymd, closed = session_info()
        rows = fetch_screener()
    except Exception as e:
        print(f"\n  [오류] {e}\n")
        return 1

    mega, small = split_buckets(rows)
    if not mega and not small:
        print("\n  [오류] 필터를 통과한 종목이 없습니다.\n")
        return 1

    d = datetime.strptime(ymd, "%Y%m%d")
    print(f"  대상 세션: {d:%Y-%m-%d} ({'마감' if closed else '장중'})")

    state = load_state()
    # 장중이면 저장하지 않는다. 미완결 세션을 기록하면 다음날 비교가 오염된다.
    if not closed and "--force" not in args:
        preview(mega, small, live=True)
        return 0
    if ymd in state["days"] and "--force" not in args:
        print(f"  {d:%m/%d} 세션은 이미 기록되어 있습니다. 리포트만 다시 만듭니다.")
    else:
        state["days"][ymd] = {"mega": mega, "small": small,
                              "at": now_kst.strftime("%Y-%m-%d %H:%M KST")}
        save_state(state)
        print(f"  저장: 대형 {len(mega)} / 중소형 {len(small)}")

    if "--quick" in args:
        rv, pullbacks = {}, []
    else:
        rv, pullbacks = scan_pool(small + mega, state, ymd)

    text = build(state, ymd, rv, pullbacks)
    print("\n" + plain(text) + "\n")
    if "--dry-run" in args:
        print("  (--dry-run: 전송하지 않았습니다)")
        return 0
    return 0 if telegram.send(text) else 1


if __name__ == "__main__":
    sys.exit(main())
