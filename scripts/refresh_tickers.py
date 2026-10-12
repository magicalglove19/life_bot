"""주 1회 실행 — 한국 300 + 미국 400 시가총액 상위 종목 갱신.
data/kr_top300.json, data/us_top400.json에 저장.
"""
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
# 2026-10 네이버 금융 PC 시가총액 페이지가 클라이언트 렌더링으로 바뀌어 HTML에 표가 없다 → 모바일 JSON API 사용.
NAVER_URL = "https://m.stock.naver.com/api/stocks/marketValue/{market}?page={page}&pageSize=100"
NAVER_PAGES = 4          # 시장당 4페이지 × 100종목. ETF를 걸러내므로 넉넉히 받는다

# 네이버 시가총액 페이지에는 ETF·ETN·스팩이 섞여 있다. FinanceDataReader의
# 종목 리스트에는 이것들이 없으므로, 대체 경로에서도 같은 구성이 되도록 걸러낸다.
_NAVER_NOISE = ("KODEX", "TIGER", "KBSTAR", "ARIRANG", "ACE ", "SOL ", "PLUS ", "RISE ",
                "HANARO", "KIWOOM", "TIMEFOLIO", "ETN", "레버리지", "인버스", "선물",
                "스팩", "리츠")


def _is_naver_noise(name: str) -> bool:
    return any(k in name for k in _NAVER_NOISE)


def _naver_page(sosok: int, page: int) -> list[dict]:
    """네이버 시가총액 순위 한 페이지. marketValue 단위는 억원."""
    url = NAVER_URL.format(market="KOSDAQ" if sosok else "KOSPI", page=page)
    raw = urllib.request.urlopen(
        urllib.request.Request(url, headers=NAVER_UA), timeout=20
    ).read()
    out = []
    for st in json.loads(raw).get("stocks") or []:
        if st.get("stockEndType") != "stock":       # ETF·ETN 제외
            continue
        name = (st.get("stockName") or "").strip()
        code = st.get("itemCode") or ""
        if not (len(code) == 6 and code.isdigit()) or _is_naver_noise(name):
            continue
        try:
            cap = int(float(st["marketValue"].replace(",", ""))) * 100_000_000   # 억원 → 원 (FDR Marcap과 단위 통일)
        except (KeyError, ValueError):
            continue
        if cap <= 0:
            continue
        out.append({
            "symbol": f"{code}{'.KQ' if sosok else '.KS'}",
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
    # 장중·지연 상태(lateInfoMsgCd ME005)에는 Marcap이 전부 NaN으로 온다 → 네이버로 넘긴다.
    if len(df) < KR_TOP_N:
        raise RuntimeError(f"FinanceDataReader: 시총 있는 종목 {len(df)}개뿐")
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


# NASDAQ 전체 목록에는 우선주·채권·유닛도 모회사 시총을 달고 섞여 있다.
_US_NOISE = re.compile(r"Preferred|Notes|Debenture|\bUnits?\b|ZONES|Warrant|Rights|%", re.I)
# "Alphabet Inc. Class A Common Stock" → "Alphabet Inc." (같은 회사 다른 주식 중복 제거·이름 정리용)
_US_SUFFIX = re.compile(r"\s+(Class [A-Z]\b.*|(Common|Capital|Ordinary) (Stock|Shares).*|American Depositary.*|Depositary.*)$", re.I)


def _dedupe_us(records: list[dict]) -> list[dict]:
    """시총 내림차순 입력에서 파생 증권을 빼고, 회사당 시총 큰 주식 하나만 남긴다."""
    seen, out = set(), []
    for r in records:
        if _US_NOISE.search(r["name"]):
            continue
        name = _US_SUFFIX.sub("", r["name"]).strip()
        if name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({**r, "name": name})
    return out


def refresh_us() -> list[dict]:
    """NASDAQ 공식 screener API(NASDAQ·NYSE·AMEX 전 종목)에서 시총 상위 400.
    실패 시 stockanalysis.com 공개 페이지로 폴백 (2026-10부터 전 세계 상위 100개만 보여서 보조용).
    """
    import requests
    from bs4 import BeautifulSoup

    # 1차: NASDAQ screener API
    # exchange=NASDAQ,NYSE 파라미터를 붙이면 2026-10부터 rows가 0개로 온다 → 빼고 전체를 받아 정렬.
    try:
        url = "https://api.nasdaq.com/api/screener/stocks?tableonly=true&limit=25&offset=0&download=true"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        }
        r = requests.get(url, headers=headers, timeout=30)
        r.raise_for_status()
        data = r.json().get("data") or {}
        rows = data.get("rows") or (data.get("table") or {}).get("rows") or []
        records = []
        for row in rows:
            sym = (row.get("symbol") or "").strip()
            cap_raw = (row.get("marketCap") or "0").replace("$", "").replace(",", "")
            try:
                cap = float(cap_raw) if cap_raw else 0.0
            except ValueError:
                cap = 0.0
            if sym and sym.isalpha() and cap > 0:
                records.append({"symbol": sym, "name": row.get("name", ""), "market_cap": cap})
        records.sort(key=lambda r: r["market_cap"], reverse=True)
        records = _dedupe_us(records)
        if len(records) >= US_TOP_N:
            return records[:US_TOP_N]
        print(f"[refresh] US NASDAQ API 결과 부족: {len(records)}개", file=sys.stderr)
    except Exception as e:
        print(f"[refresh] US NASDAQ API 실패: {e}", file=sys.stderr)

    # 2차 폴백: stockanalysis.com — 종목코드는 /stocks/<sym>/ 링크에서 읽는다 (미국 상장만).
    try:
        url = "https://stockanalysis.com/list/biggest-companies/"
        headers = {"User-Agent": "Mozilla/5.0 (compatible; LifeBot/1.0)"}
        r = requests.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        table = BeautifulSoup(r.text, "lxml").find("table")
        records = []
        for a in (table.select('a[href^="/stocks/"]') if table else []):
            symbol = a["href"].strip("/").split("/")[-1].upper()
            title = a.find(attrs={"title": True})
            name = title["title"] if title else ""
            if symbol.isalpha() and len(symbol) <= 6:
                records.append({"symbol": symbol, "name": name})
        if records:
            print(f"[refresh] US stockanalysis 폴백: {len(records)}개", file=sys.stderr)
            return records[:US_TOP_N]
    except Exception as e:
        print(f"[refresh] US stockanalysis 실패: {e}", file=sys.stderr)

    raise RuntimeError("US 종목 리스트를 가져오지 못함 (기존 파일 유지)")


def save(records: list[dict], path: Path) -> None:
    if not records:
        raise RuntimeError(f"{path.name}: 0개라 저장 안 함 (기존 파일 유지)")
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


def _load_names(path) -> dict[str, str]:
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    return {t["symbol"]: t.get("name", t["symbol"]) for t in data.get("tickers", [])}


def load_names() -> dict[str, str]:
    """{심볼: 종목명} — 한국/미국 통합. 리포트 표시용."""
    names = _load_names(US_FILE)
    names.update(_load_names(KR_FILE))
    return names


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
