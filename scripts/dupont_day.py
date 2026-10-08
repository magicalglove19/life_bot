"""듀퐁 하루 루프 — 작업 하나가 켜진 채로 10:01 ~ 15:01 KST 매시 dupont_report.py 를 돌린다.

외부 스케줄러 없이 GitHub 예약만으로 정각 실행을 맞추려는 장치다.
GitHub 예약은 1.5~5시간 늦게 시작되므로 새벽에 미리 띄워 두고, 여기서 정각까지 잠든다.

GitHub 작업은 최대 6시간까지만 돈다. 다음 정각이 이 작업의 마감(시작 + 340분)을 넘기면
같은 워크플로를 workflow_dispatch(mode=loop)로 다시 띄우고 끝낸다.
워크플로의 concurrency 가 같은 그룹을 하나씩만 돌리므로 이어받은 작업은 이 작업이 끝난 뒤 시작한다.

  python3 scripts/dupont_day.py                          # 실제 루프 (Actions)
  python3 scripts/dupont_day.py --plan=2026-10-06T05:40  # 그 시각에 시작했다면 무엇을 할지 출력만
"""
import datetime as dt
import os
import subprocess
import sys
import time
from pathlib import Path

KST = dt.timezone(dt.timedelta(hours=9))
ROOT = Path(__file__).resolve().parent.parent
SLOTS = [(10, 1), (11, 1), (12, 1), (13, 1), (14, 1), (15, 1)]
LATE_MIN = 45        # 정각에서 이만큼 지났으면 그 슬롯은 지나간 것으로 본다 (dupont_report 의 지난 봉 기준 50분 안쪽)
BUDGET_MIN = 340     # 작업 하나가 쓸 수 있는 시간 (GitHub 한도 360분에서 여유)
WORKFLOW = "dupont-kr.yml"


def sh(*cmd, check=False):
    print("$", " ".join(cmd), flush=True)
    return subprocess.run(cmd, cwd=ROOT, check=check)


def commit_state():
    sh("git", "add", "data/dupont_state.json")
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    sh("git", "commit", "-q", "-m", "dupont-kr: 가상 매매 장부 [skip ci]")
    for _ in range(3):
        sh("git", "pull", "--rebase", "--autostash", "-q", "origin", "main")
        if sh("git", "push", "-q", "origin", "HEAD:main").returncode == 0:
            return
        time.sleep(5)


def hand_off():
    """같은 워크플로를 루프 모드로 다시 띄운다 (GITHUB_TOKEN 의 workflow_dispatch 는 새 실행을 만든다)."""
    r = sh("gh", "workflow", "run", WORKFLOW, "--ref", "main", "-f", "mode=loop")
    print("[dupont-day] 다음 작업에 넘김" if r.returncode == 0 else "[dupont-day] ❌ 넘기기 실패", flush=True)


def plan(start: dt.datetime):
    """start 에 시작한 작업이 할 일: [(슬롯 시각, 'run'|'skip'|'handoff')]."""
    out, deadline = [], start + dt.timedelta(minutes=BUDGET_MIN)
    for h, m in SLOTS:
        t = start.replace(hour=h, minute=m, second=0, microsecond=0)
        if start > t + dt.timedelta(minutes=LATE_MIN):
            out.append((t, "skip"))
        elif t > deadline - dt.timedelta(minutes=15):     # 판정·커밋에 몇 분 걸린다
            out.append((t, "handoff"))
            break
        else:
            out.append((t, "run"))
    return out


def main() -> int:
    arg = next((a.split("=", 1)[1] for a in sys.argv if a.startswith("--plan=")), None)
    if arg:
        start = dt.datetime.fromisoformat(arg).replace(tzinfo=KST)
        for t, what in plan(start):
            print(f"{t:%H:%M} {what}")
        return 0

    start = dt.datetime.now(KST)
    if start.weekday() > 4:
        print(f"[dupont-day] 주말({start:%m/%d}) — 끝냅니다.")
        return 0
    print(f"[dupont-day] 시작 {start:%H:%M} KST", flush=True)
    sh(sys.executable, "scripts/dupont_report.py", "--warm")   # 첫 판정이 늦지 않게 야후 1시간봉을 미리 받는다
    for t, what in plan(start):
        if what == "skip":
            continue
        if what == "handoff":
            hand_off()
            return 0
        wait = (t - dt.datetime.now(KST)).total_seconds()
        if wait > 0:
            print(f"[dupont-day] {t:%H:%M} 까지 {wait / 60:.0f}분 대기", flush=True)
            time.sleep(wait)
        sh("git", "pull", "--rebase", "--autostash", "-q", "origin", "main")   # 다른 작업이 올린 장부 반영
        sh(sys.executable, "scripts/dupont_report.py")
        commit_state()
    print("[dupont-day] 오늘 끝", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
