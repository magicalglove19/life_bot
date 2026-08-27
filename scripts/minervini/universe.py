"""스크리닝 대상 종목 유니버스(미국주식 500종목)를 구성한다."""

from __future__ import annotations

import io
import json
import os
import time

import pandas as pd
import requests

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_SP500_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) minervini-screener/1.0"}
_CACHE_TTL = 7 * 24 * 3600  # 구성종목은 자주 안 바뀌므로 1주일 캐시


def yf_symbol(symbol: str) -> str:
    """BRK.B -> BRK-B 처럼 야후 파이낸스 표기로 변환."""
    return symbol.strip().upper().replace(".", "-")


def _cache_path(name: str) -> str:
    os.makedirs(CACHE_DIR, exist_ok=True)
    return os.path.join(CACHE_DIR, f"universe_{name}.json")


def _load_cache(name: str):
    path = _cache_path(name)
    if os.path.exists(path) and (time.time() - os.path.getmtime(path)) < _CACHE_TTL:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return None


def _save_cache(name: str, rows: list[dict]) -> None:
    with open(_cache_path(name), "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=1)


def _read_wiki_table(url: str, symbol_col: str, name_col: str, sector_col: str | None) -> list[dict]:
    html = requests.get(url, headers=_HEADERS, timeout=30).text
    tables = pd.read_html(io.StringIO(html))
    for table in tables:
        cols = {str(c).strip(): c for c in table.columns}
        if symbol_col in cols and name_col in cols:
            out = []
            for _, row in table.iterrows():
                out.append(
                    {
                        "ticker": yf_symbol(str(row[cols[symbol_col]])),
                        "name": str(row[cols[name_col]]),
                        "sector": str(row[cols[sector_col]]) if sector_col and sector_col in cols else "",
                    }
                )
            return out
    raise ValueError(f"위키 표에서 {symbol_col} 컬럼을 찾지 못했습니다: {url}")


def load_universe(name: str = "sp500", custom_file: str | None = None, limit: int | None = None) -> pd.DataFrame:
    """유니버스를 DataFrame(ticker, name, sector)으로 반환한다.

    name: 'sp500' (또는 custom_file 로 직접 목록 지정)
    """
    if custom_file:
        rows = []
        with open(custom_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.split("#", 1)[0].strip()
                if line:
                    parts = [p.strip() for p in line.split(",")]
                    rows.append(
                        {
                            "ticker": yf_symbol(parts[0]),
                            "name": parts[1] if len(parts) > 1 else parts[0],
                            "sector": parts[2] if len(parts) > 2 else "",
                        }
                    )
    else:
        rows = _load_cache(name)
        if rows is None:
            if name != "sp500":
                raise ValueError(f"알 수 없는 유니버스: {name}")
            rows = _read_wiki_table(_SP500_URL, "Symbol", "Security", "GICS Sector")
            _save_cache(name, rows)

    df = pd.DataFrame(rows).drop_duplicates(subset="ticker").reset_index(drop=True)
    if limit:
        df = df.head(limit).copy()
    return df
