"""콘솔 / CSV / HTML 출력."""

from __future__ import annotations

import html
import os
import unicodedata

import pandas as pd

RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
ITALIC = "\033[3m"

# 256색 팔레트 — 터미널에서 훨씬 또렷하다
GREEN = "\033[38;5;41m"
LIME = "\033[38;5;155m"
CYAN = "\033[38;5;44m"
BLUE = "\033[38;5;39m"
YELLOW = "\033[38;5;221m"
ORANGE = "\033[38;5;208m"
RED = "\033[38;5;203m"
PINK = "\033[38;5;211m"
PURPLE = "\033[38;5;141m"
MAGENTA = "\033[38;5;170m"
GRAY = "\033[38;5;245m"
WHITE = "\033[38;5;255m"

LIGHT_COLOR = {"초록불": GREEN, "노란불": YELLOW, "주황불": ORANGE, "빨간불": RED, "회색불": GRAY}
STATUS_COLOR = {"돌파": GREEN, "매수구간": CYAN, "형성중": GRAY, "피벗위(거래량부족)": YELLOW}


def clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


_BLOCKS = " ▏▎▍▌▋▊▉█"


def bar(ratio: float, width: int = 20, color: str = GREEN, track: str = "·") -> str:
    """0~1 비율을 부분 블록까지 쓰는 막대로."""
    ratio = clamp(ratio)
    full = ratio * width
    n = int(full)
    out = "█" * min(n, width)
    rest = width - n
    if rest > 0:
        frac = full - n
        if frac > 0.05:
            out += _BLOCKS[int(frac * 8)]
            rest -= 1
        out += DIM + GRAY + track * rest
    return color + out + RESET


def score_color(score: float) -> str:
    if score >= 80:
        return LIME
    if score >= 70:
        return GREEN
    if score >= 60:
        return YELLOW
    return GRAY


def rs_color(rs: float) -> str:
    if rs >= 90:
        return LIME
    if rs >= 80:
        return GREEN
    if rs >= 70:
        return YELLOW
    return GRAY


def rule(char: str = "─", width: int = 78, color: str = DIM) -> str:
    return color + char * width + RESET


def section(icon: str, title: str, count, desc: str, color: str = CYAN) -> None:
    """섹션 헤더 — 굵은 세로 레일 + 제목 + 개수 + 설명."""
    print()
    head = f"{color}{BOLD}▌{RESET} {color}{BOLD}{icon} {title}{RESET}"
    tail = f"{BOLD}{count}{RESET}" if count != "" else ""
    pad_to = 46
    print(f"{head}{' ' * max(1, pad_to - _w(f'{icon} {title}'))}{tail}")
    print(f"{color}▌{RESET} {DIM}{desc}{RESET}")


def _w(s: str) -> int:
    """터미널 표시 폭 (한글·전각 문자는 2칸)."""
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in str(s))


def _pad(s: str, width: int, align: str = "l") -> str:
    s = str(s)
    gap = max(0, width - _w(s))
    if align == "r":
        return " " * gap + s
    if align == "c":
        left = gap // 2
        return " " * left + s + " " * (gap - left)
    return s + " " * gap


pad = _pad  # 외부에서 쓰는 별칭
width = _w


def table(headers: list[str], rows: list[list], aligns: str | None = None, colors: list[list | None] | None = None) -> str:
    aligns = aligns or "l" * len(headers)
    widths = [_w(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], _w(cell))

    sep = "─"
    out = [
        BOLD + "  ".join(_pad(h, widths[i], "c") for i, h in enumerate(headers)) + RESET,
        DIM + "  ".join(sep * widths[i] for i in range(len(headers))) + RESET,
    ]
    for r_i, row in enumerate(rows):
        cells = []
        for i, cell in enumerate(row):
            txt = _pad(cell, widths[i], aligns[i])
            c = colors[r_i][i] if colors and colors[r_i] and colors[r_i][i] else ""
            cells.append(f"{c}{txt}{RESET}" if c else txt)
        out.append("  ".join(cells))
    return "\n".join(out)


def print_regime(r) -> None:
    color = LIGHT_COLOR.get(r.light, "")
    print()
    print(BOLD + "═" * 78 + RESET)
    print(f"{BOLD}📊 시장 국면 ({r.symbol}){RESET}")
    print(BOLD + "═" * 78 + RESET)
    if r.price == r.price:
        print(
            f"  {r.symbol} {r.price:,.2f}   50일선 {r.ma50:,.2f}   200일선 {r.ma200:,.2f}   "
            f"52주고점대비 {r.pct_from_high:+.1f}%"
        )
        flags = [
            ("50일선 위", r.above_ma50),
            ("200일선 위", r.above_ma200),
            ("200일선 상승", r.ma200_up),
            ("50>200", r.ma50 > r.ma200),
        ]
        print("  " + "   ".join(f"{GREEN}✔{RESET} {n}" if v else f"{RED}✘{RESET} {n}" for n, v in flags))
    print(
        f"  시장 폭: Stage 2 통과 {r.breadth_stage2:.1f}%   200일선 위 {r.breadth_above_ma200:.1f}%"
    )
    print(f"  판정: {color}{BOLD}{r.light}{RESET}  →  권장 노출도 {BOLD}{r.exposure}{RESET}")
    print(f"  {DIM}{r.comment}{RESET}")


def save_csv(df: pd.DataFrame, path: str) -> str:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    df.to_csv(path, index=False, encoding="utf-8-sig")
    return path




# ────────────────────────────────────────────────────────────────────────
#  정밀 진단 카드 (터미널) — 한 종목을 시각적으로 뜯어본다
# ────────────────────────────────────────────────────────────────────────

def render_detail(
    *,
    ticker: str,
    name: str = "",
    sector: str = "",
    price: float,
    checks: list,
    values: list,
    labels: list,
    rs_rating: float,
    v,                      # VCPResult
    total_score=None,
    rank=None,
    fundamentals=None,
    entry=None,
    stop=None,
    stop_pct=None,
    shares=None,
    position_value=None,
    risk_amount=None,
    pct_from_high=None,
    stage2_since=None,
    stage2_days: int = 0,
    setup_since=None,
    setup_days: int = 0,
    high52=None,
    high52_date=None,
    width_: int = 78,
) -> None:
    import numpy as np

    passed = sum(1 for c in checks if c)
    accent = GREEN if passed == 8 else (YELLOW if passed >= 6 else RED)
    rail = f"{accent}▌{RESET}"
    thin = f"{accent}{DIM}│{RESET}"

    print()
    print(f"{accent}{BOLD}▛{'▀' * (width_ - 1)}{RESET}")
    rank_txt = f"{PURPLE}{BOLD}{rank}위{RESET}  " if rank else ""
    score_txt = ""
    if total_score is not None:
        score_txt = f"{score_color(total_score)}{BOLD}{total_score:.1f}점{RESET}"
    left = f"{rank_txt}{WHITE}{BOLD}{ticker}{RESET}  {name}"
    plain = f"{str(rank) + '위  ' if rank else ''}{ticker}  {name}"
    gap = max(1, width_ - 2 - _w(plain) - _w(f"{total_score:.1f}점" if total_score is not None else ""))
    print(f"{rail} {left}{' ' * gap}{score_txt}")
    bits = [x for x in [sector, f"${price:,.2f}",
                        (f"52주고점 {pct_from_high:+.1f}%" if pct_from_high is not None and np.isfinite(pct_from_high) else "")] if x]
    print(f"{rail} {DIM}{'  ·  '.join(bits)}{RESET}")
    print(thin)

    tt_color = GREEN if passed == 8 else YELLOW
    print(f"{thin} {BOLD}Trend Template{RESET}   {bar(passed / 8, 16, tt_color, '·')} {tt_color}{BOLD}{passed}/8{RESET}"
          f"  {GREEN + BOLD + '통과 — Stage 2 상승추세' + RESET if passed == 8 else RED + '탈락' + RESET}")
    for label, ok, val in zip(labels, checks, values):
        mark = f"{GREEN}✔{RESET}" if ok else f"{RED}✘{RESET}"
        body = f"{RESET if ok else DIM}{_pad(label, 28)}{RESET}"
        print(f"{thin}   {mark} {body} {DIM}{val}{RESET}")
    print(thin)

    if np.isfinite(rs_rating):
        rc = rs_color(rs_rating)
        print(f"{thin} {BOLD}RS 등급{RESET}  {rc}{BOLD}{rs_rating:>3.0f}{RESET}  {bar(rs_rating / 100, 24, rc)}"
              f"  {DIM}상위 {100 - rs_rating:.0f}% · 기준 70{RESET}")
        print(thin)

    st_color = STATUS_COLOR.get(v.status, GRAY)
    print(f"{thin} {BOLD}VCP{RESET}  {st_color}{BOLD}{v.status}{RESET}" + (f"   {DIM}{v.note}{RESET}" if v.note else ""))
    if v.depths:
        maxd = max(v.depths) or 1
        n = len(v.depths)
        for i, d in enumerate(v.depths, 1):
            last = i == n
            col = LIME if last else (CYAN if i == n - 1 else BLUE)
            tail = f"  {LIME}◀ 마지막 수축{RESET}" if last else ""
            print(f"{thin}   {DIM}{i}차{RESET} {col}{d:>5.1f}%{RESET}  {bar(d / maxd, 26, col, ' ')}{tail}")
        if np.isfinite(v.dryup_ratio):
            dc = LIME if v.dryup_ratio <= 0.85 else (YELLOW if v.dryup_ratio <= 1.0 else GRAY)
            print(f"{thin}   {DIM}거래량 마름{RESET} {dc}{v.dryup_ratio:>5.2f}{RESET}  "
                  f"{bar(clamp(1 - (v.dryup_ratio - 0.4) / 0.9), 26, dc)}  {DIM}0.85 이하면 양호{RESET}")
        if np.isfinite(v.pivot):
            print(f"{thin}   {DIM}피벗{RESET} {WHITE}{BOLD}{v.pivot:,.2f}{RESET} "
                  f"{DIM}(현재가 대비 {v.distance_to_pivot:+.1f}%){RESET}   "
                  f"{DIM}베이스 {v.base_bars}봉 · VCP {v.score:.0f}점{RESET}")
    print(thin)

    f = fundamentals
    if f is not None and not f.error:
        parts = []
        if np.isfinite(f.eps_yoy):
            c = LIME if f.eps_yoy >= 25 else (YELLOW if f.eps_yoy > 0 else RED)
            parts.append(f"EPS {c}{f.eps_yoy:+.0f}%{RESET}")
        if np.isfinite(f.sales_yoy):
            c = LIME if f.sales_yoy >= 10 else (YELLOW if f.sales_yoy > 0 else RED)
            parts.append(f"매출 {c}{f.sales_yoy:+.0f}%{RESET}")
        if np.isfinite(f.margin_now):
            parts.append(f"순이익률 {WHITE}{f.margin_now:.1f}%{RESET}")
        cc = LIME if f.code33 == "충족" else (YELLOW if f.code33.startswith("부분") else GRAY)
        parts.append(f"Code33 {cc}{f.code33}{RESET}")
        print(f"{thin} {BOLD}펀더멘털{RESET}  {DIM}(전년동기비){RESET}  " + "   ".join(parts))
        print(thin)

    from .history import fmt as _fmt

    rows = []
    if stage2_since is not None:
        rows.append(("Stage 2 진입", _fmt(stage2_since), f"{stage2_days}거래일째",
                     f"{LIME}◀ 갓 진입{RESET}" if stage2_days <= 5 else ""))
    if v.base_start_date is not None:
        rows.append(("베이스 시작", _fmt(v.base_start_date), f"{v.base_bars}봉", ""))
    if v.pivot_date is not None:
        rows.append(("피벗 형성", _fmt(v.pivot_date), f"{v.pivot:,.2f}", ""))
    if setup_since is not None:
        rows.append(("VCP 셋업 등장", _fmt(setup_since), f"{setup_days}거래일째",
                     f"{LIME}◀ 갓 나온 셋업{RESET}" if setup_days <= 3 else ""))
    if v.breakout_date is not None:
        rows.append(("피벗 돌파", _fmt(v.breakout_date), "", f"{GREEN}◀ 돌파 이후{RESET}"))
    if high52_date is not None:
        rows.append(("52주 고점", _fmt(high52_date), f"{high52:,.2f}" if high52 else "", ""))
    if rows:
        print(f"{thin} {BOLD}타임라인{RESET}")
        for label, date, extra, tag in rows:
            print(f"{thin}   {DIM}{_pad(label, 15)}{RESET}{CYAN}{date}{RESET}   {DIM}{_pad(extra, 12)}{RESET}{tag}")
        print(thin)

    if entry is not None and np.isfinite(entry) and shares:
        print(f"{thin} {BOLD}실행 계획{RESET}")
        print(f"{thin}   진입 {GREEN}{BOLD}{entry:,.2f}{RESET} {DIM}(피벗 위 매수 스톱){RESET}"
              f"   →   손절 {RED}{BOLD}{stop:,.2f}{RESET} {RED}(-{stop_pct:.1f}%){RESET}")
        print(f"{thin}   수량 {WHITE}{BOLD}{shares:,}주{RESET} × {entry:,.2f} = "
              f"{WHITE}{BOLD}${position_value:,.0f}{RESET}   {DIM}최대 손실 ${risk_amount:,.0f}{RESET}")
    print(f"{accent}{BOLD}▙{'▄' * (width_ - 1)}{RESET}")


def progress(done: int, total: int, elapsed: float, label: str = "") -> None:
    """한 줄 진행률 + 남은 시간 추정 (\\r로 덮어쓴다)."""
    ratio = done / total if total else 1.0
    eta = (elapsed / ratio - elapsed) if ratio > 0 else 0
    from .timing import human

    line = (f"  {bar(ratio, 24, CYAN)} {BOLD}{ratio * 100:>3.0f}%{RESET} "
            f"{DIM}{done}/{total}{RESET}  {DIM}경과 {human(elapsed)}"
            + (f" · 남은 시간 약 {human(eta)}" if ratio < 0.999 else "")
            + f"{RESET}  {label}")
    print("\r\033[K" + line, end="", flush=True)


def progress_done(elapsed: float, label: str) -> None:
    from .timing import human

    took = f" {DIM}({human(elapsed)}){RESET}" if elapsed and elapsed >= 0.5 else ""
    print(f"\r\033[K  {GREEN}✔{RESET} {label}{took}", flush=True)


# ══════════════════════════════════════════════════════════════════════
#  HTML 리포트 — 터미널에서 보는 내용을 그대로 담는다
# ══════════════════════════════════════════════════════════════════════

_CSS = """
:root{--bg:#fff;--fg:#16191d;--muted:#6b7280;--line:#e5e7eb;--card:#f8fafc;--card2:#f1f5f9;
--green:#0f8a4c;--red:#c62828;--blue:#1558d6;--amber:#b45309;--purple:#7c3aed;--track:#e2e8f0;}
@media (prefers-color-scheme:dark){:root:not([data-theme="light"]){--bg:#0e1116;--fg:#e6e9ef;--muted:#9aa4b2;
--line:#232a34;--card:#151a21;--card2:#1b2129;--green:#4ade80;--red:#f87171;--blue:#60a5fa;
--amber:#fbbf24;--purple:#c4b5fd;--track:#252c36;}}
:root[data-theme="dark"]{--bg:#0e1116;--fg:#e6e9ef;--muted:#9aa4b2;--line:#232a34;--card:#151a21;
--card2:#1b2129;--green:#4ade80;--red:#f87171;--blue:#60a5fa;--amber:#fbbf24;--purple:#c4b5fd;--track:#252c36;}

*{box-sizing:border-box;}
body{background:var(--bg);color:var(--fg);margin:0;padding:26px 20px 80px;
font:14px/1.55 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Segoe UI",Roboto,sans-serif;}
.wrap{max-width:1440px;margin:0 auto;}
h1{font-size:23px;margin:0 0 4px;letter-spacing:-.015em;}
.sub{color:var(--muted);font-size:13px;margin-bottom:20px;}

nav{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:22px;}
nav a{font-size:12px;padding:5px 11px;border:1px solid var(--line);border-radius:99px;
color:var(--muted);text-decoration:none;white-space:nowrap;}
nav a:hover{color:var(--fg);border-color:var(--muted);}

.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:26px;}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:11px 13px;}
.card .k{color:var(--muted);font-size:10.5px;text-transform:uppercase;letter-spacing:.07em;}
.card .v{font-size:19px;font-weight:650;margin-top:3px;line-height:1.25;}

section{margin:34px 0 0;scroll-margin-top:12px;}
.shead{display:flex;align-items:baseline;gap:10px;border-left:3px solid var(--accent,var(--blue));
padding-left:11px;margin-bottom:4px;}
.shead h2{font-size:16px;margin:0;letter-spacing:-.01em;}
.shead .cnt{font-size:13px;font-weight:650;color:var(--muted);}
.sdesc{color:var(--muted);font-size:12.5px;padding-left:14px;margin:0 0 11px;}
.s-green{--accent:var(--green);} .s-blue{--accent:var(--blue);} .s-amber{--accent:var(--amber);}
.s-purple{--accent:var(--purple);} .s-red{--accent:var(--red);}

.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;}
table{border-collapse:collapse;width:100%;font-size:12.5px;font-variant-numeric:tabular-nums;}
th,td{padding:7px 9px;text-align:right;white-space:nowrap;border-bottom:1px solid var(--line);}
th{background:var(--card);position:sticky;top:0;font-size:10.5px;color:var(--muted);
letter-spacing:.05em;text-align:right;cursor:pointer;user-select:none;}
th:hover{color:var(--fg);} th.sorted{color:var(--blue);}
th .arw{opacity:.6;font-size:9px;margin-left:3px;}
tbody tr:last-child td{border-bottom:none;}
tbody tr:hover{background:var(--card);}
.tk{font-weight:700;text-align:left;}
th:nth-child(1),td:nth-child(1),th:nth-child(2),td:nth-child(2),
th:nth-child(3),td:nth-child(3),th:nth-child(4),td:nth-child(4){text-align:left;}
.pos{color:var(--green);} .neg{color:var(--red);}
.bk{color:var(--green);font-weight:650;} .buy{color:var(--blue);font-weight:650;}
.form{color:var(--muted);} .warn{color:var(--amber);}
.date{color:var(--muted);font-size:11.5px;} .fresh{color:var(--green);font-weight:700;}
.hint{color:var(--muted);font-size:12px;margin:0 0 8px 2px;}
.empty{color:var(--muted);font-size:13px;padding:14px 4px;}

/* 시장 국면 */
.regime{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:15px 17px;}
.regime .line{display:flex;flex-wrap:wrap;gap:18px;align-items:center;margin-bottom:9px;}
.regime b{font-weight:650;}
.flags{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;margin:8px 0;}
.flag.on{color:var(--green);} .flag.off{color:var(--red);}
.light{font-size:17px;font-weight:700;}
.l-green{color:var(--green);} .l-yellow{color:var(--amber);}
.l-orange{color:var(--amber);} .l-red{color:var(--red);} .l-gray{color:var(--muted);}

/* 섹터 요약 */
.sect{display:grid;grid-template-columns:170px 1fr;gap:6px 14px;align-items:baseline;
background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;font-size:12.5px;}
.sect .nm{color:var(--blue);font-weight:600;}
.sect .tks{display:flex;flex-wrap:wrap;gap:4px 8px;}
.sect .tks span{font-weight:650;}
.g80{color:var(--green);} .g70{color:var(--green);opacity:.8;}
.g60{color:var(--amber);} .g0{color:var(--muted);}
@media(max-width:640px){.sect{grid-template-columns:1fr;}}

/* 정밀 진단 카드 */
.dcard{border:1px solid var(--line);border-left:4px solid var(--accent,var(--green));
border-radius:10px;background:var(--card);padding:16px 18px;margin-bottom:16px;}
.dhead{display:flex;flex-wrap:wrap;align-items:baseline;gap:9px;margin-bottom:2px;}
.dhead .rk{color:var(--purple);font-weight:700;font-size:13px;}
.dhead .tkr{font-size:19px;font-weight:750;letter-spacing:-.01em;}
.dhead .nm{color:var(--fg);font-size:14px;}
.dhead .sc{margin-left:auto;font-size:17px;font-weight:700;color:var(--green);}
.dsub{color:var(--muted);font-size:12.5px;margin-bottom:14px;}
.dgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:16px 26px;}
.blk h4{font-size:11px;text-transform:uppercase;letter-spacing:.07em;color:var(--muted);
margin:0 0 8px;font-weight:600;}
.pill{display:inline-block;font-size:11px;padding:1px 7px;border-radius:99px;
background:var(--card2);color:var(--fg);font-weight:650;letter-spacing:0;text-transform:none;}
.pill.ok{color:var(--green);} .pill.no{color:var(--red);}
ul.chk{list-style:none;margin:0;padding:0;font-size:12.5px;}
ul.chk li{display:flex;gap:7px;padding:2.5px 0;align-items:baseline;}
ul.chk .m{width:12px;flex:none;} ul.chk .m.y{color:var(--green);} ul.chk .m.n{color:var(--red);}
ul.chk .lb{flex:none;width:168px;}
ul.chk .vl{color:var(--muted);font-variant-numeric:tabular-nums;}
ul.chk li.off .lb{color:var(--muted);}

.brow{display:grid;grid-template-columns:34px 52px 1fr auto;gap:9px;align-items:center;
font-size:12.5px;padding:2.5px 0;font-variant-numeric:tabular-nums;}
.brow .lb{color:var(--muted);}
.btrack{background:var(--track);border-radius:3px;height:9px;overflow:hidden;}
.btrack i{display:block;height:100%;border-radius:3px;background:var(--blue);}
.btrack i.last{background:var(--green);} .btrack i.dry{background:var(--amber);}
.btrack i.dryok{background:var(--green);} .btrack i.rs{background:var(--green);}
.brow .tag{color:var(--green);font-size:11.5px;font-weight:650;}

table.tl{width:100%;font-size:12.5px;border-collapse:collapse;}
table.tl td{border:none;padding:2.5px 0;text-align:left;white-space:nowrap;}
table.tl td.l{color:var(--muted);width:112px;}
table.tl td.d{color:var(--blue);font-variant-numeric:tabular-nums;width:104px;}
table.tl td.x{color:var(--muted);font-variant-numeric:tabular-nums;}
table.tl td.t{color:var(--green);font-weight:650;}

.plan{font-size:13px;line-height:1.9;}
.plan b{font-weight:700;font-variant-numeric:tabular-nums;}
.plan .in{color:var(--green);} .plan .out{color:var(--red);}
.plan .mut{color:var(--muted);font-size:12px;}
.fund{font-size:12.5px;display:flex;flex-wrap:wrap;gap:6px 18px;}
.fund b{font-weight:700;}

.note{margin-top:36px;color:var(--muted);font-size:12px;line-height:1.75;
border-top:1px solid var(--line);padding-top:16px;}
"""

_JS = """
(function(){
  function key(td){
    var t=(td.textContent||'').trim();
    if(!t||t==='-') return null;
    var d=t.match(/^(\\d{4})-(\\d{2})-(\\d{2})/);
    if(d) return Date.UTC(+d[1],+d[2]-1,+d[3]);
    var n=t.replace(/[,$\\s%+]/g,'').match(/^-?\\d+(\\.\\d+)?/);
    if(n && /\\d/.test(t)) return parseFloat(n[0]);
    return t;
  }
  document.querySelectorAll('table.srt').forEach(function(tbl){
    var tb=tbl.tBodies[0]; if(!tb) return;
    var ths=[].slice.call(tbl.tHead.rows[0].cells);
    var cur=-1, asc=true;
    ths.forEach(function(th,i){
      th.addEventListener('click',function(){
        if(cur===i){asc=!asc;} else {cur=i; asc=(i===0);}
        var rows=[].slice.call(tb.rows);
        rows.sort(function(a,b){
          var x=key(a.cells[i]), y=key(b.cells[i]);
          if(x===null&&y===null) return 0;
          if(x===null) return 1;
          if(y===null) return -1;
          var r=(typeof x==='number'&&typeof y==='number')?x-y:String(x).localeCompare(String(y),'ko');
          return asc?r:-r;
        });
        rows.forEach(function(r){tb.appendChild(r);});
        ths.forEach(function(t2,j){
          t2.classList.toggle('sorted',j===i);
          var a=t2.querySelector('.arw'); if(a) a.remove();
          if(j===i){var sp=document.createElement('span');sp.className='arw';
            sp.textContent=asc?'\\u25B2':'\\u25BC';t2.appendChild(sp);}
        });
      });
    });
  });
})();
"""

_LIGHT_CLASS = {"초록불": "l-green", "노란불": "l-yellow", "주황불": "l-orange", "빨간불": "l-red", "회색불": "l-gray"}


def _esc(x) -> str:
    return html.escape("" if x is None else str(x))


def _cell_class(col: str, val: str) -> str:
    if col == "VCP상태":
        return {"돌파": "bk", "매수구간": "buy", "형성중": "form"}.get(val, "warn")
    if col in ("셋업등장", "Stage2진입", "베이스시작", "피벗형성", "돌파일", "52주고점일"):
        return "date"
    if col == "타점경과":
        if val == "-":
            return "date"
        try:
            return "date" if val == "오늘" or int(val.replace("일", "").replace("⚠", "")) <= 5 else "warn"
        except (TypeError, ValueError):
            return "date"
    if col == "셋업일수":
        try:
            return "fresh" if 0 < int(val) <= 3 else "date"
        except (TypeError, ValueError):
            return "date"
    if col in ("52주고점대비",) and val.startswith("-"):
        return "neg"
    if col in ("EPS성장%", "매출성장%") and val not in ("-", ""):
        return "neg" if val.startswith("-") else "pos"
    return ""


def _table_html(df, empty_msg: str = "해당하는 종목이 없습니다.") -> str:
    import pandas as pd

    if df is None or len(df) == 0:
        return f'<div class="empty">{_esc(empty_msg)}</div>'
    cols = list(df.columns)
    head = "".join(f"<th>{_esc(c)}</th>" for c in cols)
    body = []
    for _, row in df.iterrows():
        tds = []
        for c in cols:
            v = "" if pd.isna(row[c]) else str(row[c])
            klass = "tk" if c == "티커" else _cell_class(c, v)
            tds.append(f'<td class="{klass}">{_esc(v)}</td>' if klass else f"<td>{_esc(v)}</td>")
        body.append("<tr>" + "".join(tds) + "</tr>")
    return ('<div class="scroll"><table class="srt"><thead><tr>' + head
            + "</tr></thead><tbody>" + "".join(body) + "</tbody></table></div>")


def _section_html(anchor, icon, title, count, desc, klass, inner) -> str:
    return (f'<section id="{anchor}" class="{klass}"><div class="shead">'
            f"<h2>{icon} {_esc(title)}</h2><span class=\"cnt\">{_esc(count)}</span></div>"
            f'<p class="sdesc">{_esc(desc)}</p>{inner}</section>')


def _regime_html(r) -> str:
    import numpy as np

    if not np.isfinite(r.price):
        return f'<div class="regime">{_esc(r.comment)}</div>'
    flags = [("50일선 위", r.above_ma50), ("200일선 위", r.above_ma200),
             ("200일선 상승", r.ma200_up), ("50일선 > 200일선", r.ma50 > r.ma200)]
    fl = "".join(f'<span class="flag {"on" if v else "off"}">{"✔" if v else "✘"} {_esc(n)}</span>' for n, v in flags)
    return f"""<div class="regime">
<div class="line"><span><b>{_esc(r.symbol)}</b> {r.price:,.2f}</span>
<span>50일선 {r.ma50:,.2f}</span><span>200일선 {r.ma200:,.2f}</span>
<span>52주 고점 대비 {r.pct_from_high:+.1f}%</span></div>
<div class="flags">{fl}</div>
<div class="line"><span>Stage 2 통과 <b>{r.breadth_stage2:.1f}%</b></span>
<span>200일선 위 <b>{r.breadth_above_ma200:.1f}%</b></span></div>
<div class="line"><span class="light {_LIGHT_CLASS.get(r.light, 'l-gray')}">{_esc(r.light)}</span>
<span>권장 노출도 <b>{_esc(r.exposure)}</b></span></div>
<div class="sdesc" style="padding:0;margin:2px 0 0">{_esc(r.comment)}</div></div>"""


def _sector_html(groups) -> str:
    def g(score):
        return "g80" if score >= 80 else "g70" if score >= 70 else "g60" if score >= 60 else "g0"

    rows = []
    for sector, items in groups:
        tks = "".join(f'<span class="{g(sc)}">{_esc(tk)}</span>' for tk, sc in items)
        rows.append(f'<div class="nm">{_esc(sector)} <span class="g0">{len(items)}</span></div>'
                    f'<div class="tks">{tks}</div>')
    legend = ('<div class="nm">색 기준</div><div class="tks">'
              '<span class="g80">80점+</span><span class="g70">70점+</span>'
              '<span class="g60">60점+</span><span class="g0">60점 미만</span></div>')
    return f'<div class="sect">{"".join(rows)}{legend}</div>'


def _bar_html(pct: float, klass: str = "") -> str:
    pct = max(0.0, min(100.0, pct))
    return f'<div class="btrack"><i class="{klass}" style="width:{pct:.1f}%"></i></div>'


def _detail_html(d: dict) -> str:
    import numpy as np

    passed = sum(1 for _, ok, _ in d["checks"] if ok)
    accent = "var(--green)" if passed == 8 else "var(--amber)"
    chk = "".join(
        f'<li class="{"" if ok else "off"}"><span class="m {"y" if ok else "n"}">{"✔" if ok else "✘"}</span>'
        f'<span class="lb">{_esc(lb)}</span><span class="vl">{_esc(vl)}</span></li>'
        for lb, ok, vl in d["checks"]
    )
    blocks = [
        f'<div class="blk"><h4>Trend Template <span class="pill {"ok" if passed == 8 else "no"}">'
        f'{passed}/8</span></h4><ul class="chk">{chk}</ul></div>'
    ]

    # RS + VCP 수축
    vb = []
    if np.isfinite(d["rs"]):
        vb.append(f'<div class="brow"><span class="lb">RS</span><span><b>{d["rs"]:.0f}</b></span>'
                  f'{_bar_html(d["rs"], "rs")}<span class="lb">상위 {100 - d["rs"]:.0f}%</span></div>')
    depths = d["vcp"].get("depths") or []
    if depths:
        mx = max(depths) or 1
        for i, dep in enumerate(depths, 1):
            last = i == len(depths)
            tag = "◀ 마지막" if last else ""
            vb.append(f'<div class="brow"><span class="lb">{i}차</span><span>{dep:.1f}%</span>'
                      f'{_bar_html(dep / mx * 100, "last" if last else "")}'
                      f'<span class="tag">{tag}</span></div>')
    dry = d["vcp"].get("dryup")
    if dry is not None and np.isfinite(dry):
        ok = dry <= 0.85
        vb.append(f'<div class="brow"><span class="lb">거래량</span><span>{dry:.2f}</span>'
                  f'{_bar_html(max(0, (1 - (dry - 0.4) / 0.9)) * 100, "dryok" if ok else "dry")}'
                  f'<span class="lb">0.85↓ 양호</span></div>')
    if vb:
        note = d["vcp"].get("note") or ""
        vb.append(f'<div class="sdesc" style="padding:0;margin:8px 0 0">'
                  f'피벗 <b>{d["vcp"]["pivot"]}</b> · 베이스 {d["vcp"]["base_bars"]}봉 · '
                  f'VCP {d["vcp"]["score"]:.0f}점{(" · " + _esc(note)) if note else ""}</div>')
        blocks.append(f'<div class="blk"><h4>VCP <span class="pill">{_esc(d["vcp"]["status"])}</span></h4>'
                      + "".join(vb) + "</div>")

    if d.get("timeline"):
        tl = "".join(f'<tr><td class="l">{_esc(a)}</td><td class="d">{_esc(b)}</td>'
                     f'<td class="x">{_esc(c)}</td><td class="t">{_esc(t)}</td></tr>'
                     for a, b, c, t in d["timeline"])
        blocks.append(f'<div class="blk"><h4>타임라인</h4><table class="tl">{tl}</table></div>')

    tail = []
    if d.get("fundamentals"):
        f = d["fundamentals"]
        tail.append('<div class="blk"><h4>펀더멘털 (전년동기비)</h4><div class="fund">'
                    + "".join(f"<span>{_esc(k)} <b>{_esc(v)}</b></span>" for k, v in f)
                    + "</div></div>")
    if d.get("plan"):
        p = d["plan"]
        tail.append(f'<div class="blk"><h4>실행 계획</h4><div class="plan">'
                    f'진입 <b class="in">{p["entry"]}</b> <span class="mut">(피벗 위 매수 스톱)</span>'
                    f' &rarr; 손절 <b class="out">{p["stop"]}</b> <span class="out">({p["stop_pct"]})</span><br>'
                    f'수량 <b>{p["shares"]}주</b> · 투자금액 <b>{p["value"]}</b> '
                    f'<span class="mut">· 최대 손실 {p["risk"]}</span></div></div>')

    return (f'<div class="dcard" style="--accent:{accent}"><div class="dhead">'
            + (f'<span class="rk">{d["rank"]}위</span>' if d.get("rank") else "")
            + f'<span class="tkr">{_esc(d["ticker"])}</span><span class="nm">{_esc(d["name"])}</span>'
            + (f'<span class="sc">{d["score"]:.1f}점</span>' if d.get("score") is not None else "")
            + f'</div><div class="dsub">{_esc(d["sub"])}</div>'
            + f'<div class="dgrid">{"".join(blocks)}{"".join(tail)}</div></div>')


def save_html(path: str, regime, meta: dict, sections: list, sector_groups: list, details: list) -> str:
    """터미널 리포트 전체를 한 장의 HTML로."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    cards = [
        ("스캔 종목", f"{meta['scanned']}"),
        ("Stage 2 통과", f"{meta['stage2']}"),
        ("VCP 셋업", f"{meta['vcp']}"),
        ("표시 기준", f"{meta['threshold']:.0f}점+" if meta.get("threshold") is not None else "전체"),
        ("정렬", meta.get("sort", "종합점수 높은 순")),
        ("시장 국면", regime.light),
        ("권장 노출도", regime.exposure),
    ]
    card_html = "".join(f'<div class="card"><div class="k">{_esc(k)}</div>'
                        f'<div class="v">{_esc(v)}</div></div>' for k, v in cards)

    nav = ['<a href="#regime">📊 시장 국면</a>']
    body = [_section_html("regime", "📊", "시장 국면", regime.symbol,
                          "지수 추세와 시장 폭 — 개별 종목보다 먼저 봐야 한다", "s-blue",
                          _regime_html(regime))]

    for sec in sections:
        nav.append(f'<a href="#{sec["anchor"]}">{sec["icon"]} {_esc(sec["title"])}</a>')
        inner = _table_html(sec["df"], sec.get("empty", "해당하는 종목이 없습니다."))
        if sec.get("hint"):
            inner = f'<p class="hint">{_esc(sec["hint"])}</p>' + inner
        body.append(_section_html(sec["anchor"], sec["icon"], sec["title"],
                                  f'{len(sec["df"]) if sec["df"] is not None else 0}종목',
                                  sec["desc"], sec["klass"], inner))

    if sector_groups:
        nav.append('<a href="#sectors">📋 Stage 2 전체</a>')
        body.append(_section_html("sectors", "📋", "Stage 2 통과 전체", f"{meta['stage2']}종목",
                                  "Trend Template 8개 기준을 모두 통과한 종목 (섹터별)", "s-blue",
                                  _sector_html(sector_groups)))
    if details or meta.get("detail_dropped"):
        nav.append('<a href="#detail">🔎 정밀 진단</a>')
        inner = ""
        if meta.get("detail_dropped"):
            names = ", ".join(f"{t} ({d}일 경과)" for t, d in meta["detail_dropped"])
            inner += f'<p class="hint">연장 구간이라 제외: {_esc(names)}</p>'
        inner += ("".join(_detail_html(d) for d in details) if details
                  else '<div class="empty">매수 타점 안에 든 종목이 없습니다.</div>')
        body.append(_section_html("detail", "🔎", "정밀 진단", f"상위 {len(details)}종목",
                                  meta.get("detail_desc", "8개 기준 · RS · VCP 수축 구조 · 타임라인 · 실행 계획"),
                                  "s-green", inner))

    doc = f"""<title>미너비니 스크리너 리포트</title>
<style>{_CSS}</style>
<div class="wrap">
<h1>마크 미너비니 스크리너 — Trend Template + VCP</h1>
<div class="sub">{_esc(meta['generated'])} · {_esc(meta['universe'])} · {_esc(regime.comment)}</div>
<nav>{''.join(nav)}</nav>
<div class="cards">{card_html}</div>
{''.join(body)}
<div class="note">
<b>진입가</b>는 피벗 +0.1%(미너비니의 "피벗 위 5~10센트")에 걸어두는 매수 스톱 기준입니다.
<b>손절가</b>는 마지막 수축의 저점과 최대 손절폭({meta['max_stop']}%) 중 더 타이트한 쪽입니다.
<b>수량·투자금액</b>은 계좌 {_esc(meta['account'])}, 1회 리스크 {meta['risk']}% 기준으로 역산했습니다.<br>
<b>셋업일수 / Stage2일수</b>는 과거 시점으로 되감아 기준을 다시 판정해 구한 값입니다 —
프로그램을 오늘 처음 돌려도 실제 진입일이 나옵니다. 숫자가 작을수록 갓 나온 자리입니다.<br>
표의 <b>열 제목을 클릭</b>하면 그 기준으로 정렬되고, 한 번 더 누르면 역순입니다.<br><br>
이 리포트는 기술적 스크리닝 결과이며 투자 자문이 아닙니다. 실제 매매 전에 차트와 재무제표를 직접 확인하세요.
</div>
</div>
<script>{_JS}</script>"""
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(doc)
    return path
