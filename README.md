# Life Bot

텔레그램으로 매일 루틴 자동 전송. GitHub Actions + 무료 API.

## 스케줄

| 시간 (KST) | 요일 | 내용 |
|---|---|---|
| 05:00 | 월~토 | 변곡점·강남자리 스캔 결과 + RSI (TQQQ/SOXL/TECL/SCHD) + 차트 패턴(컵위드핸들/더블바텀/V라인/갭상승) |
| 09:00 | 매일 | 이탈리아어 단어 10 + 문장 3 |
| 14:00 | 매일 | 미국 경제 뉴스 3개 + 한국어 번역 |
| 13:50 | 월~금 | 유목민식 8일선/정배열 스크리너 (패턴 A·B, 장중 시세 기준) |
| 05:30 | 월~토 | 미너비니 SEPA 스크리너 (S&P 500) — 2편으로 분할 발송 |
| 19:00 | 월~금 | 초등 영어 단어 10 (월~목: 새 단어 / 금: 주간 랜덤 20) |

## 데이터 소스 (전부 무료)

- **주식 가격·RSI**: yfinance (Yahoo)
- **한국 시가총액 Top 300**: pykrx
- **미국 뉴스**: MarketWatch RSS
- **LLM (번역·생성)**: Groq 무료 티어 (Llama 3.3 70B)

## 초기 세팅

[SETUP.md](SETUP.md) 참고.

## 폴더 구조

```
life-bot/
├── .github/workflows/   # GitHub Actions cron
├── scripts/
│   ├── common/          # 공용 모듈 (telegram, groq)
│   └── *.py             # 개별 작업 스크립트
└── data/                # 상태 파일 (자동 갱신)
```

## 로컬 테스트

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

export TELEGRAM_BOT_TOKEN=xxx
export TELEGRAM_CHAT_ID=xxx
export GROQ_API_KEY=xxx

python scripts/italian_lesson.py
```
