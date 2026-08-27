"""실행 시간 학습 — 지난 실행 기록으로 다음 실행의 예상 시간을 추정한다."""

from __future__ import annotations

import json
import os

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "cache")
_PATH = os.path.join(CACHE_DIR, "timing.json")

# 기록이 없을 때 쓰는 초기 추정치 (실측 기반)
DEFAULTS = {
    "download_per_100": 9.0,   # 티커 100개 다운로드에 걸리는 초
    "fund_per_ticker": 0.13,   # 펀더멘털 1종목당 초 (8스레드 병렬)
    "analysis_per_100": 0.6,   # 지표·VCP 계산 100종목당 초
    "overhead": 2.0,
}


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        with open(_PATH, encoding="utf-8") as fh:
            saved = json.load(fh)
        for k in DEFAULTS:
            if isinstance(saved.get(k), (int, float)) and saved[k] > 0:
                data[k] = float(saved[k])
    except Exception:
        pass
    return data


def update(key: str, value: float, weight: float = 0.4) -> None:
    """지수이동평균으로 부드럽게 갱신 (한 번의 느린 실행에 휘둘리지 않게)."""
    if not (value > 0):
        return
    data = load()
    data[key] = round(data.get(key, value) * (1 - weight) + value * weight, 3)
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(_PATH, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)
    except Exception:
        pass


def estimate(n_tickers: int, cached: bool, with_fundamentals: bool = True, fund_cached: bool = False) -> float:
    """전체 스캔 예상 소요 시간(초)."""
    t = load()
    total = t["overhead"] + n_tickers / 100 * t["analysis_per_100"]
    total += 0.8 if cached else n_tickers / 100 * t["download_per_100"]
    if with_fundamentals and not fund_cached:
        # 경험상 Trend Template 통과 비율은 10~25% 수준
        total += n_tickers * 0.18 * t["fund_per_ticker"]
    return total


def human(seconds: float) -> str:
    seconds = max(0.0, seconds)
    if seconds < 1:
        return "1초 미만"
    if seconds < 60:
        return f"{seconds:.0f}초"
    m, s = divmod(int(round(seconds)), 60)
    return f"{m}분 {s}초" if s else f"{m}분"
