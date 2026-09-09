"""주 1회 실행 — 한국 300 + 미국 400 시가총액 상위 종목 갱신.
data/kr_top300.json, data/us_top400.json에 저장.
"""
import html as H
import json
import re
import sys
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import timeutil  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KR_FILE = DATA_DIR / "kr_top300.json"
US_FILE = DATA_DIR / "us_top400.json"

KR_TOP_N = 300
US_TOP_N = 400


NAVER_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
NAVER_URL = "https://finance.naver.com/sise/sise_market_sum.naver?sosok={sosok}&page={page}"
_NAVER_LINK = re.compile(r'code=(\d{6})"[^>]*class="tltle">([^<]+)</a>')
_NAVER_NUM = re.compile(r'<td class="number">(.*?)</td>', re.S)
NAVER_PAGES = 8          # 시장당 8페이지 × 50종목. ETF를 걸러내므로 넉넉히 받는다

# 네이버 시가총액 페이지에는 ETF·ETN·스팩이 섞여 있다. FinanceDataReader의
# 종목 리스트에는 이것들이 없으므로, 대체 경로에서도 같은 구성이 되도록 걸러낸다.
_NAVER_NOISE = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "ACE ", "SOL ", "PLUS ", "RISE ",
                "HANARO", "KIWOOM", "TIMEFOLIO", "ETN", "레버리지", "인버스", "선물",
                "스팩", "리츠")


def _is_naver_noise(name: str) -> bool:
    return any(k in name for k in _NAVER_NOISE)


def _naver_clean(cell: str) -> str:
    return H.unescape(re.sub(r"<[^>]+>", " ", cell)).replace(",", "").replace("%", "").strip()


def _naver_page(sosok: int, page: int) -> list[dict]:
    """네이버 시가총액 페이지 한 장. 컬럼 순서는
    현재가 · 전일비 · 등락률 · 액면가 · 시가총액(억) · 상장주식수 · ... 이다."""
    url = NAVER_URL.format(sosok=sosok, page=page)
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=NAVER_UA), timeout=20
    ).read()
    text = raw.decode("euc-kr", errors="replace")

    out = []
    for row in re.findall(r"<tr[^>]*>(.*?)</tr>", text, re.S):
        m = _NAVER_LINK.search(row)
        if not m:
            continue
        nums = [_naver_clean(x) for x in _NAVER_NUM.findall(row)]
        if len(nums) < 5:
            continue
        try:
            cap = int(float(nums[4])) * 100_000_000     # 억원 → 원 (FDR Marcap과 단위 통일)
        except ValueError:
            continue
        if cap <= 0:
            continue
        name = H.unescape(m.group(2)).strip()
        if _is_naver_noise(name):
            continue
        out.append({
            "symbol": f"{m.group(1)}{'.KQ' if sosok else '.KS'}",
            "name": name,
            "market": "KOSDAQ" if sosok else "KOSPI",
            "market_cap": cap,
        })
    return out


def _refresh_korea_naver() -> list[dict]:
    """대비책 — 네이버 금융 시가총액 페이지에서 상위 종목을 긁는다.

    FinanceDataReader의 종목 리스트 API가 죽었을 때 쓴다(2026-09 실제로 404가 났다).
    개별 시세 조회는 멀쩡한데 리스트 API만 죽는 경우가 있어 이쪽만 대체한다.
    """
    records = []
    for sosok in (0, 1):                      # 0=코스피, 1=코스닥
        for page in range(1, NAVER_PAGES + 1):
            try:
                rows = _naver_page(sosok, page)
            except Exception as e:
                print(f"[refresh] 네이버 {sosok}/{page} 실패: {e}", file=sys.stderr)
                continue
            if not rows:
                break
            records.extend(rows)
            time.sleep(0.3)                   # 예의상 간격
    if not records:
        raise RuntimeError("네이버 시가총액 페이지에서 아무것도 못 받음")
    records.sort(key=lambda r: r["market_cap"], reverse=True)
    print(f"[refresh] KR: {len(records)}개 수집 → 상위 {KR_TOP_N} (네이버 대체)")
    return records[:KR_TOP_N]


def refresh_korea() -> list[dict]:
    """KRX 시총 상위 300. FinanceDataReader 우선, 실패하면 네이버로 대체."""
    try:
        return _refresh_korea_fdr()
    except Exception as e:
        print(f"[refresh] FinanceDataReader 실패 → 네이버로 대체: {e}", file=sys.stderr)
        return _refresh_korea_naver()


def _refresh_korea_fdr() -> list[dict]:
    """FinanceDataReader로 KRX 전체 종목 시총 상위 300."""
    import FinanceDataReader as fdr

    df = fdr.StockListing("KRX")
    if df is None or df.empty:
        raise RuntimeError("FinanceDataReader: KRX 종목 리스트 조회 실패")

    # 컬럼명은 라이브러리 버전에 따라 다를 수 있음 (Symbol/Code, Marcap)
    cols = {c.lower(): c for c in df.columns}
    sym_col = cols.get("code") or cols.get("symbol")
    name_col = cols.get("name")
    cap_col = cols.get("marcap") or cols.get("marketcap")
    market_col = cols.get("market")
    if not (sym_col and name_col and cap_col):
        raise RuntimeError(f"FinanceDataReader 컬럼 식별 실패: {list(df.columns)}")

    df = df.dropna(subset=[cap_col])
    df = df[df[cap_col] > 0]
    df = df.sort_values(by=cap_col, ascending=False).head(KR_TOP_N)

    records = []
    for _, row in df.iterrows():
        code = str(row[sym_col]).zfill(6)
        market = (str(row[market_col]).upper() if market_col else "KOSPI")
        suffix = ".KS" if "KOSPI" in market else ".KQ"
        records.append({
            "symbol": f"{code}{suffix}",
            "name": str(row[name_col]),
            "market": market,
            "market_cap": int(row[cap_col]),
        })
    print(f"[refresh] KR: {len(records)}개 (FinanceDataReader)")
    return records


def refresh_us() -> list[dict]:
    """stockanalysis.com 공개 페이지에서 시총 상위 400 파싱.
    실패 시 NASDAQ 공식 screener API로 폴백.
    """
    import requests
    from bs4 import BeautifulSoup

    # 1차: stockanalysis.com (공개 HTML 테이블, 로봇 비차단)
    try:
        records = []
        for page in range(1, 5):  # 100 per page * 4 = 400
            url = f"https://stockanalysis.com/list/biggest-companies/"
            headers = {"User-Agent": "Mozilla/5.0 (compatible; LifeBot/1.0)"}
            r = requests.get(url, headers=headers, timeout=20)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "lxml")
            table = soup.find("table")
            if not table:
                break
            rows = table.find_all("tr")[1:]  # 헤더 제외
            for row in rows:
                cells = [c.get_text(strip=True) for c in row.find_all(["td", "th"])]
                if len(cells) < 3:
                    continue
                # 컬럼 추정: [No, Symbol, Name, MarketCap, ...]
                symbol = cells[1] if len(cells) > 1 else ""
                name = cells[2] if len(cells) > 2 else ""
                if symbol and symbol.isalpha() and len(symbol) <= 6:
                    records.append({"symbol": symbol, "name": name})
            break  # stockanalysis는 단일 페이지에 많은 항목
        if records:
            return records[:US_TOP_N]
    except Exception as e:
        print(f"[refresh] US stockanalysis 실패: {e}", file=sys.stderr)

    # 2차 폴백: NASDAQ screener API
    try:
        url = (
            "https://api.nasdaq.com/api/screener/stocks"
            f"?tableonly=true&limit={US_TOP_N}&offset=0&exchange=NASDAQ,NYSE"
            "&download=true"
        )
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        data = r.json()
        rows = data.get("data", {}).get("rows", []) or data.get("data", {}).get("table", {}).get("rows", [])
        records = []
        for row in rows:
            sym = row.get("symbol", "").strip()
            cap_raw = row.get("marketCap", "0").replace("$", "").replace(",", "").replace("B", "e9").replace("M", "e6")
            try:
                cap = float(cap_raw) if cap_raw else 0.0
            except ValueError:
                cap = 0.0
            if sym and sym.isalpha():
                records.append({"symbol": sym, "name": row.get("name", ""), "market_cap": cap})
        records.sort(key=lambda r: r.get("market_cap", 0), reverse=True)
        return records[:US_TOP_N]
    except Exception as e:
        print(f"[refresh] US NASDAQ API 실패: {e}", file=sys.stderr)
        raise


def save(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "updated_at": timeutil.now().isoformat(timespec="seconds"),
        "count": len(records),
        "tickers": records,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> int:
    errors = []

    try:
        kr = refresh_korea()
        save(kr, KR_FILE)
        print(f"[refresh] KR 저장: {len(kr)}개")
    except Exception as e:
        errors.append(f"KR: {e}")
        print(f"[refresh] KR 실패: {e}", file=sys.stderr)

    try:
        us = refresh_us()
        save(us, US_FILE)
        print(f"[refresh] US 저장: {len(us)}개")
    except Exception as e:
        errors.append(f"US: {e}")
        print(f"[refresh] US 실패: {e}", file=sys.stderr)

    if errors:
        # 텔레그램 알림
        try:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            from common import telegram
            telegram.send("⚠️ 시가총액 갱신 일부 실패:\n" + "\n".join(errors))
        except Exception:
            pass
        return 1 if len(errors) == 2 else 0
    return 0


def load_kr_symbols() -> list[str]:
    """아침 스캐너에서 사용."""
    if not KR_FILE.exists():
        return []
    data = json.loads(KR_FILE.read_text(encoding="utf-8"))
    return [t["symbol"] for t in data.get("tickers", [])]


def load_us_symbols() -> list[str]:
    if not US_FILE.exists():
        return []
    data = json.loads(US_FILE.read_text(encoding="utf-8"))
    return [t["symbol"] for t in data.get("tickers", [])]


if __name__ == "__main__":
    sys.exit(main())
