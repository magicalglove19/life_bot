"""실행 시각 유틸.

GitHub Actions 러너는 UTC로 돈다. 스크립트에서 datetime.now()를 그대로 쓰면
한국 시간과 9시간이 어긋나서, 예약 시각이 UTC 자정을 넘는 작업(아침 브리핑,
이탈리아어)은 **전날 날짜**가 찍힌다. 시각 표기는 전부 이 모듈을 거친다.
"""
import datetime as dt

KST = dt.timezone(dt.timedelta(hours=9))


def now() -> dt.datetime:
    """한국 시간 기준 현재 시각."""
    return dt.datetime.now(KST)


def today() -> dt.date:
    """한국 시간 기준 오늘 날짜."""
    return now().date()


def stamp(fmt: str = "%Y-%m-%d (%a)") -> str:
    return now().strftime(fmt)


def weekday() -> int:
    """월=0 ... 일=6 (한국 시간 기준)."""
    return now().weekday()
