"""평일 15:00 KST — 강창권 상한가 후 눌림목 스크리너 (국내 KOSPI + KOSDAQ).

상한가·장대양봉·급등·신고가가 나온 종목을 추적하다가, 그 뒤에 이어지는
세 가지 일봉 조정 패턴(A/B/C)이 완성되는 날의 종가 매수 타점을 찾는다.

장 마감(15:30) 전에 도착해야 종가로 살 수 있어서 15:00에 보낸다.
그 시점 가격은 아직 종가가 아니므로 잠정 판정이며, 확정 결과는 맥에서
'상한가 스크리너 실행.command' 로 다시 본다.

판정 로직은 원본(2026 강창권 상한가 후/kcg)을 그대로 벤더링한 것이며,
여기서는 결과를 텔레그램용으로 요약만 한다.
"""
import os
import sys
import datetime as dt
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import telegram
from kcg import data, notify, ranking, screener, universe, watchlist
from kcg.config import Config

KST = dt.timezone(dt.timedelta(hours=9))

UNIVERSE = int(os.environ.get("KCG_UNIVERSE", "0"))      # 0 이면 전 종목 (4스레드로 약 80초)
MIN_SCORE = float(os.environ.get("KCG_MIN_SCORE", "60"))
MARKET = os.environ.get("KCG_MARKET", "ALL")
WORKERS = int(os.environ.get("KCG_WORKERS", "4"))        # 크게 잡으면 네이버가 막는다
DETAIL_TOP = int(os.environ.get("KCG_DETAIL_TOP", "10"))

# 도착 허용 시간대 (KST). 예약 크론 '40 1 * * 1-5' 은 GitHub 예약 큐가
# 평균 4시간 20분 밀리는 것을 역산한 값이라, 큐가 빠른 날에는 10:40 에 실행된다.
# 그때 "오늘 종가 매수"를 보내면 장 초반 가격으로 판정한 엉뚱한 메시지가 된다.
# 창을 벗어나면 스스로 건너뛴다 (수동 실행은 KCG_FORCE=1 로 무시).
WINDOW_START = (14, 0)
WINDOW_END = (16, 30)


def in_window(now: dt.datetime) -> bool:
    cur = (now.hour, now.minute)
    return WINDOW_START <= cur <= WINDOW_END


def main() -> int:
    dry = "--dry-run" in sys.argv
    force = "--force" in sys.argv or os.environ.get("KCG_FORCE") == "1"

    now = dt.datetime.now(KST)
    if not (dry or force):
        if now.weekday() > 4:
            print(f"[kcg] 주말({now:%m/%d}) — 건너뜁니다.")
            return 0
        if not in_window(now):
            print(f"[kcg] 실행 시각 {now:%H:%M} KST 가 발송 창"
                  f"({WINDOW_START[0]:02d}:{WINDOW_START[1]:02d}~"
                  f"{WINDOW_END[0]:02d}:{WINDOW_END[1]:02d}) 밖입니다 — 건너뜁니다.")
            return 0

    cfg = Config()
    cfg.market = MARKET
    cfg.min_score = MIN_SCORE

    rows = universe.load(market=cfg.market, exclude_spac=cfg.exclude_spac,
                         exclude_preferred=cfg.exclude_preferred)
    # 러너는 매번 새 파일시스템이라 watchlist.json 이 없다. 그래도 거래대금 상위가
    # 상한가·급등 종목을 대부분 데려오고, 남는 자리는 시총 상위로 채운다.
    watched = list(watchlist.load())
    rows = ranking.pick(rows, UNIVERSE, must_have=watched)
    codes = [r["code"] for r in rows]
    meta = {r["code"]: r for r in rows}
    print(f"[kcg] 유니버스 {len(codes)}종목 (요청 {UNIVERSE or '전체'})", flush=True)

    frames = data.download(codes, cfg.history_days, WORKERS, use_cache=True, tag="kcg")
    if not frames:
        print("[kcg] 일봉을 하나도 받지 못했습니다.", file=sys.stderr)
        return 1
    print(f"[kcg] 일봉 {len(frames)}종목 수집", flush=True)

    res = screener.scan(frames, meta, cfg)
    print(f"[kcg] 스캔 {res.scanned} · 매수 시그널 {len(res.buys)} · 조정 중 {len(res.tracking)}",
          flush=True)

    body = notify.build(res, [], frames, cfg, detail_top=DETAIL_TOP)
    if dry:
        print(body)
        return 0
    return 0 if telegram.send(body) else 1


if __name__ == "__main__":
    sys.exit(main())
