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
    """pykrx로 KOSPI + KOSDAQ 시총 상위 300."""
    from pykrx import stock

    # 최근 영업일 찾기 (최대 10일 역탐색)
    today = datetime.now()
    date_str = None
    for i in range(10):
        d = (today - timedelta(days=i)).strftime("%Y%m%d")
        try:
            tickers = stock.get_market_ticker_list(d, market="KOSPI")
            if tickers:
                date_str = d
                break
        except Exception:
            continue
    if not date_str:
        raise RuntimeError("pykrx: 최근 영업일 조회 실패")

    records = []
    for market in ("KOSPI", "KOSDAQ"):
        suffix = ".KS" if market == "KOSPI" else ".KQ"
        tickers = stock.get_market_ticker_list(date_str, market=market)
        cap_df = stock.get_market_cap(date_str, market=market)
        if cap_df is None or cap_df.empty:
            continue
        for code in cap_df.index:
            if code not in tickers:
                continue
            try:
                name = stock.get_market_ticker_name(code)
            except Exception:
                name = code
            records.append({
                "symbol": f"{code}{suffix}",
                "name": name,
                "market": market,
                "market_cap": int(cap_df.loc[code, "시가총액"]),
            })
    records.sort(key=lambda r: r["market_cap"], reverse=True)
    top = records[:KR_TOP_N]
    print(f"[refresh] KR: {date_str} 기준 {len(top)}개")
    return top


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
