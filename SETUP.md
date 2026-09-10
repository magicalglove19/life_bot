# Life Bot 세팅 가이드

전체 4단계. 순서대로 따라가시면 30분 내 완료됩니다.

---

## 1️⃣ 텔레그램 봇 생성

### (1) 봇 만들기
1. 텔레그램 앱에서 **@BotFather** 검색 → 대화 시작
2. `/newbot` 입력
3. 봇 이름 입력 (예: `My Life Bot`)
4. 봇 사용자명 입력 (반드시 `bot`으로 끝나야 함, 예: `my_life_bot`)
5. BotFather가 **Bot Token** 을 알려줍니다 → 복사해서 어딘가에 메모
   - 예시 형태: `7891234567:AAFxxx_yyyyyyyyyyyyy`

### (2) Chat ID 확인
1. 방금 만든 봇과 대화 시작 (검색해서 `/start`)
2. 아무 메시지나 한 번 보내기 (예: "hi")
3. 브라우저에서 다음 URL 접속 (`<BOT_TOKEN>` 부분만 본인 토큰으로):
   ```
   https://api.telegram.org/bot<BOT_TOKEN>/getUpdates
   ```
4. JSON에서 `"chat":{"id": 숫자 ...}` 부분의 **숫자**가 Chat ID
5. 메모해두기

---

## 2️⃣ Groq API 키 발급 (무료)

1. https://console.groq.com 접속 → Google 로그인
2. 좌측 메뉴 **API Keys** → **Create API Key**
3. 이름 아무거나 (예: `life-bot`) → 생성
4. 키 복사 (`gsk_...` 형태) → 메모
   - ⚠️ 키는 생성 시에만 보임, 닫으면 다시 못 봄

무료 티어 제한 (2026년 기준): 모델별 1일 ~1,000~14,400회. 우리는 일 5~6회라 여유 충분.

---

## 3️⃣ GitHub 레포 생성 & 업로드

### (1) 로컬 초기화
터미널에서:
```bash
cd /path/to/life-bot   # 레포를 받아둔 경로
git init
git add .
git commit -m "initial: life-bot setup"
```

### (2) GitHub에 새 레포 만들기
1. https://github.com/new
2. 레포 이름: `life-bot` (또는 원하는 이름)
3. **Private** 선택 (추천)
4. README / .gitignore / license 체크 **안 함** (이미 있음)
5. Create repository

### (3) 푸시
GitHub이 보여주는 명령어 참고 (또는 아래):
```bash
git branch -M main
git remote add origin https://github.com/<본인-아이디>/life-bot.git
git push -u origin main
```

---

## 4️⃣ GitHub Secrets 등록

1. GitHub 레포 페이지 → **Settings**
2. 좌측 **Secrets and variables** → **Actions**
3. **New repository secret** 버튼으로 아래 3개 등록:

| Name | Value |
|---|---|
| `TELEGRAM_BOT_TOKEN` | 1단계에서 받은 Bot Token |
| `TELEGRAM_CHAT_ID` | 1단계에서 받은 Chat ID |
| `GROQ_API_KEY` | 2단계에서 받은 Groq 키 |

> 주도주 봇(`judoju`)은 네이버 금융을 읽으므로 추가 시크릿이 없습니다.

---

## 5️⃣ 첫 실행 테스트

### (1) 텔레그램 테스트
레포에 들어가서 **Actions** 탭 → **italian** workflow → **Run workflow** 클릭 → 30초~1분 후 텔레그램 확인.

### (2) 시가총액 리스트 생성 (아침 스캔 전 필수)
**Actions** 탭 → **refresh-tickers** workflow → **Run workflow** 클릭
→ 완료되면 `data/kr_top300.json`, `data/us_top400.json`이 커밋됨

### (3) 아침 브리핑 테스트
**Actions** → **morning-stocks** → **Run workflow**
→ 10~15분 걸림 (700개 종목 스캔)
→ 완료되면 텔레그램으로 종합 브리핑 도착

---

## 🕒 스케줄 확인

자동 실행 시각 (한국 시간):

| 시간 | 요일 | Workflow |
|---|---|---|
| 05:00 | 월~토 | morning-stocks |
| 09:00 | 매일 | italian |
| 13시경 | 월~금 | ma8-screener |
| 14:00 | 매일 | us-news |
| 05:30 | 월~토 | minervini |
| — | 수동 | kid-english (예약 해제) |
| 일요일 00:00 | 주 1회 | refresh-tickers |
| 09:30·12:00·15:00 | 월~금 | judoju (외부 트리거 권장 — 아래 참고) |
| 15:00 | 월~금 | kcg — 강창권 상한가 후 눌림목 (국내) |

### ⏰ kcg 도 같은 시간대 문제를 쓴다

`kcg` 는 장 마감(15:30) 전에 도착해야 종가로 살 수 있어서 15:00 이 생명이다.
judoju 의 1500 슬롯과 같은 역산값 `cron: '40 1 * * 1-5'` 를 쓴다.

큐가 빠른 날에는 10:40 KST 에 실행될 수 있는데, 그때 "오늘 종가 매수"를 보내면
장 초반 가격으로 판정한 엉뚱한 메시지가 된다. 그래서 `kcg_report.py` 가
**발송 창(14:00~16:30 KST)** 을 벗어나면 스스로 건너뛴다.
정확한 시각이 필요하면 `{"event_type":"kcg"}` 로 repository_dispatch 를 때린다.
수동 실행(Actions → Run workflow)은 기본으로 이 가드를 무시한다.

### ⏰ judoju 는 예약으로는 제 시간에 못 온다

공식 문서는 15분 지연이라고 하지만 이 레포 실측은 다르다. UTC 03~05시 슬롯에서
**4.3~4.6시간** 밀린 적이 있고, 12:00 KST 가 정확히 03:00 UTC 다.
예약만 걸면 주도주 스냅샷이 장 마감 뒤에 찍힌다.

그래서 `judoju` 는 **repository_dispatch** 를 주 경로로 쓴다. 무료 외부 스케줄러
(cron-job.org 등)에서 아래를 정각에 때리면 지연 없이 즉시 실행된다.

```
POST https://api.github.com/repos/magicalglove19/life_bot/dispatches
Authorization: Bearer <PAT (repo 권한)>
Accept: application/vnd.github+json
Body: {"event_type":"judoju","client_payload":{"slot":"1200"}}
```

`slot` 을 `0930` / `1200` / `1500` 으로 바꿔 세 개 등록한다 (월~금).
PAT 은 GitHub > Settings > Developer settings > Personal access tokens 에서
`repo` 권한으로 만든다.

예약(schedule)도 백업으로 걸려 있지만, 슬롯 시간대를 벗어나면 `judoju.py` 가
상태 파일 오염을 막기 위해 **스스로 건너뛴다**. 즉 늦게 온 스냅샷은 버려진다.

⚠️ **GitHub Actions 주의사항**
- 무료 티어에서 크론은 **최대 15분 지연** 가능 (공식 문서 언급)
- 즉, 08:00 예약이 08:15에 실행될 수 있음. 치명적이라면 cron 시간을 10~15분 앞당기세요
- 60일 이상 레포에 활동이 없으면 스케줄 자동 비활성화됨 (Push만 간헐적으로 해주면 됨)

---

## 🛠️ 로컬에서 테스트하고 싶을 때

```bash
cd /path/to/life-bot   # 레포를 받아둔 경로
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 환경변수 설정
export TELEGRAM_BOT_TOKEN="7891234567:AAFxxx..."
export TELEGRAM_CHAT_ID="123456789"
export GROQ_API_KEY="gsk_xxx..."

# 개별 테스트
python scripts/italian_lesson.py
python scripts/us_news.py
python scripts/rsi_report.py
python scripts/minervini_report.py
```

---

## ❓ 문제 해결

### 텔레그램 메시지가 안 와요
- Bot Token / Chat ID 맞게 등록됐는지 확인
- 봇과 최소 1번 대화(`/start`) 했는지 확인
- GitHub Actions 로그에서 `[telegram] HTTP` 에러 확인

### Groq API 오류
- 키 형식 `gsk_...` 확인
- 모델이 단종되면 `scripts/common/groq_client.py`가 후보 모델을 순회하고, 그래도 안 되면 사용 가능한 모델을 자동 탐색함
- 원인이 궁금하면 Actions 탭 → `groq-doctor` 워크플로 수동 실행 → 텔레그램으로 진단 결과 도착

### 아침 스캔에서 종목이 하나도 안 잡혀요
- 정상일 수 있음 (해당 조건에 맞는 종목이 없음)
- `refresh-tickers`를 먼저 1회 수동 실행했는지 확인
- Actions 로그에서 "KR/US 시가총액 파일 없음" 메시지 없는지 확인

---

## 🔄 스크립트 수정하고 싶을 때

로컬에서 편집 → 커밋/푸시 → 다음 예정 시간에 반영됨. 즉시 테스트는 **Actions 탭 → Run workflow** 수동 실행.

---

## ⏱️ 12:00 주도주만 맥에서 트리거하는 이유

GitHub의 예약(schedule) 실행은 이 레포 실측으로 **2~4.6시간** 밀린다. 지연 폭이
예약 슬롯의 UTC 시간대를 따라가는데, 그 조합상 **10:00~13:20 KST 사이에 실행되게
만드는 예약값이 존재하지 않는다.** judoju의 12:00 슬롯이 여기 걸린다.

그래서 이 슬롯만 맥이 직접 GitHub을 찌른다 (`repository_dispatch`는 즉시 실행된다).

### 설치 (한 번만)

1. **GitHub 토큰 발급** — https://github.com/settings/personal-access-tokens/new
   - Repository access: **Only select repositories** → `life_bot`
   - Permissions → Repository permissions → **Contents: Read and write**
   - 만료일은 짧게(90일~1년). 만료되면 아래 2번만 다시 하면 된다

2. **토큰을 맥 키체인에 보관** — 토큰을 복사한 상태(클립보드)에서 실행한다.
   저장 전에 GitHub에 유효성을 물어보고, 유효할 때만 저장한다.
   ```bash
   ./scripts/local/set-token.sh
   ```
   토큰이 화면에 찍히거나 셸 기록에 남지 않는다. 나중에 만료되면 새 토큰을
   복사한 뒤 이 명령만 다시 실행하면 된다 (기존 항목은 자동 정리된다).

3. **예약 설치**
   ```bash
   ./scripts/local/install-judoju-trigger.sh
   ```

### 확인 / 해제

```bash
./scripts/local/trigger.sh judoju 1200      # 지금 즉시 실행시켜 보기
tail -f ~/Library/Logs/lifebot-trigger.log  # 실행 기록
launchctl unload ~/Library/LaunchAgents/com.lifebot.judoju-1200.plist   # 해제
```

맥이 12시에 자고 있었다면 깨어날 때 실행되고, 13:30(1200 슬롯 허용 한계)을 넘겼다면
`judoju.py`가 스스로 건너뛴다. 잘못된 시각의 데이터가 상태 파일을 오염시키지 않는다.
