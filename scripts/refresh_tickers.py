"""주 1회 실행 — 한국 300 + 미국 400 시가총액 상위 종목 갱신.
data/kr_top300.json, data/us_top400.json에 저장.
"""
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
KR_FILE = DATA_DIR / "kr_top300.json"
US_FILE = DATA_DIR / "us_top400.json"

KR_TOP_N = 300
US_TOP_N = 400


def refresh_korea() -> list[dict]:
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
        "updated_at": datetime.now().isoformat(timespec="seconds"),
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
