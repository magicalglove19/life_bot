# -*- coding: utf-8 -*-
"""차트 패턴 감지 로직 (Chart Pattern Analyzer 앱과 동일 기준).
- Cup with Handle (컵위드핸들)
- Double Bottom (더블 바텀)
- Triple Bottom (트리플 바텀, 미국 전용, 2026-10 추가)
- V-Line (V자 반등)  ※ 기본 비활성 — 아래 참조
- Gap Up (갭 상승) — 미국은 강화 조건(detect_gap_up_strict, 2026-10)
- 두번째 정배열 (미국 전용, 2026-10 추가)
chart_patterns.py / confluence.py에서 import해서 사용.

■ 2026-09 백테스트 반영 사항
1) 룩어헤드 제거: find_local_minima/maxima는 기준봉 뒤 window(5 또는 3)봉까지
   봐야 그 봉이 진짜 저점/고점이었는지 확정된다. 예전에는 돌파일을 그대로
   발생일로 썼기 때문에, 아직 확정되지 않은 피벗을 근거로 신호를 냈다.
   이제 발생일 = max(돌파일, 피벗확정일)로 보정한다.
2) V라인 기본 제외: 2022 하락장 40.2% / 2023 46.4% / 2025~26 40.7%로
   세 구간 모두 SPY 대비 승률이 크게 낮고 평균 MAE가 -13%에 달했다.
   DETECTORS에서 빼고, 필요하면 include_vline=True로 켠다.
"""
import pandas as pd
import numpy as np

MIN_W = 5    # 컵/더블바텀 피벗 확정에 필요한 봉 수
VLINE_W = 3  # V라인 피벗 확정에 필요한 봉 수


def find_local_minima(series, window=5):
    vals = series.values
    n = len(vals)
    idx = []
    for i in range(window, n - window):
        seg = vals[i - window:i + window + 1]
        if vals[i] == seg.min():
            idx.append(i)
    return idx


def find_local_maxima(series, window=5):
    vals = series.values
    n = len(vals)
    idx = []
    for i in range(window, n - window):
        seg = vals[i - window:i + window + 1]
        if vals[i] == seg.max():
            idx.append(i)
    return idx


def detect_double_bottom(df):
    """두 저점의 가격이 비슷하고(4% 이내) 사이에 8% 이상 반등한 고점이 있으며,
    두번째 저점 이후 그 고점(넥라인)을 돌파하는 시점을 발생일로 본다."""
    results = []
    close = df['Close']
    minima_idx = find_local_minima(close, window=MIN_W)
    if len(minima_idx) < 2:
        return results

    for i in range(len(minima_idx) - 1):
        i1 = minima_idx[i]
        for j in range(i + 1, len(minima_idx)):
            i2 = minima_idx[j]
            gap = i2 - i1
            if gap < 10 or gap > 90:
                continue
            low1 = close.iloc[i1]
            low2 = close.iloc[i2]
            if low1 <= 0:
                continue
            diff_pct = abs(low1 - low2) / low1
            if diff_pct > 0.04:
                continue

            between = close.iloc[i1:i2 + 1]
            peak = between.max()
            base = min(low1, low2)
            if base <= 0:
                continue
            rise_pct = (peak - base) / base
            if rise_pct < 0.08:
                continue

            after = close.iloc[i2 + 1:]
            bo = None
            for k in range(len(after)):
                if after.iloc[k] > peak:
                    bo = i2 + 1 + k
                    break
            if bo is None:
                continue
            # 두번째 저점은 i2+MIN_W 이 되어야 저점으로 확정된다
            known = max(bo, i2 + MIN_W)
            if known >= len(close):
                continue
            results.append({
                'date': close.index[known],
                'pattern': '더블 바텀',
                'detail': f'저점 {low1:.2f}/{low2:.2f} (차이 {diff_pct*100:.1f}%), 넥라인 {peak:.2f} 돌파'
            })
    return results


def detect_cup_with_handle(df):
    """왼쪽 고점(left rim) -> 12~35% 하락한 둥근 바닥(cup bottom) ->
    왼쪽과 15% 이내인 오른쪽 고점(right rim) -> 오른쪽 고점 대비 3~15% 조정(handle)
    -> 오른쪽 고점 돌파, 순서로 이어지는 지점을 찾는다."""
    results = []
    close = df['Close']
    n = len(close)

    maxima = find_local_maxima(close, window=MIN_W)
    minima = find_local_minima(close, window=MIN_W)
    if not maxima or not minima:
        return results

    for li in maxima:
        left_rim = close.iloc[li]
        if left_rim <= 0:
            continue

        bottoms = [m for m in minima if m > li and (m - li) <= 130]
        for bi in bottoms:
            depth = (left_rim - close.iloc[bi]) / left_rim
            if depth < 0.12 or depth > 0.35:
                continue
            if (bi - li) < 15:  # 컵의 왼쪽 절반이 너무 짧으면 제외
                continue

            right_rims = [rm for rm in maxima if rm > bi and (rm - bi) <= 130]
            for ri in right_rims:
                right_rim = close.iloc[ri]
                if abs(right_rim - left_rim) / left_rim > 0.15:
                    continue
                total_len = ri - li
                if total_len < 35 or total_len > 260:  # 대략 7주~1년
                    continue

                handles = [hm for hm in minima if ri < hm <= ri + 20]
                for hi in handles:
                    handle_low = close.iloc[hi]
                    pullback = (right_rim - handle_low) / right_rim
                    if pullback < 0.03 or pullback > 0.15:
                        continue

                    search_end = min(n, hi + 21)
                    after = close.iloc[hi + 1:search_end]
                    bo = None
                    for k in range(len(after)):
                        if after.iloc[k] > right_rim:
                            bo = hi + 1 + k
                            break
                    if bo is None:
                        continue
                    # 핸들 저점은 hi+MIN_W 이 되어야 확정된다
                    known = max(bo, hi + MIN_W)
                    if known >= n:
                        continue
                    results.append({
                        'date': close.index[known],
                        'pattern': '컵위드핸들',
                        'detail': f'컵 깊이 {depth*100:.1f}%, 핸들 조정 {pullback*100:.1f}%, 돌파가 {right_rim:.2f}'
                    })
    return results


def detect_v_line(df, decline_days=10, drop_pct=0.08, rally_days=10, rally_pct=0.08):
    """짧은 기간(최대 10거래일) 안에 8% 이상 급락한 뒤,
    이후 10거래일 안에 다시 8% 이상 급반등하는 뾰족한 V자 저점을 찾는다.

    ※ 백테스트상 성과가 나빠 DETECTORS 기본 목록에서 제외되어 있다."""
    results = []
    close = df['Close']
    n = len(close)
    minima_idx = find_local_minima(close, window=VLINE_W)

    for idx in minima_idx:
        if idx < decline_days or idx > n - 2:
            continue
        pre_high = close.iloc[max(0, idx - decline_days):idx].max()
        low = close.iloc[idx]
        if pre_high <= 0 or low <= 0:
            continue
        decline = (pre_high - low) / pre_high
        if decline < drop_pct:
            continue

        post_window = close.iloc[idx: min(n, idx + rally_days + 1)]
        rally_high = post_window.max()
        rally = (rally_high - low) / low
        if rally < rally_pct:
            continue

        rally_pos = int(post_window.values.argmax())
        conf = idx + rally_pos
        if conf == idx:
            continue
        known = max(conf, idx + VLINE_W)
        if known >= n:
            continue
        results.append({
            'date': close.index[known],
            'pattern': 'V라인',
            'detail': f'{decline*100:.1f}% 급락 후 {rally*100:.1f}% 급반등 (저점 {low:.2f})'
        })
    return results


def detect_gap_up(df, threshold=0.03):
    """전일 종가 대비 시가가 threshold 이상 갭으로 뜨고, 당일 저가가
    전일 종가를 채우지 않은 경우(갭이 메워지지 않음)를 감지한다.
    한국용. 미국은 detect_gap_up_strict 를 쓴다 (scan_dataframe(us_only=True))."""
    results = []
    close = df['Close']
    open_ = df['Open']
    low = df['Low']

    for i in range(1, len(df)):
        prev_close = close.iloc[i - 1]
        today_open = open_.iloc[i]
        today_low = low.iloc[i]
        if prev_close <= 0:
            continue
        gap = (today_open - prev_close) / prev_close
        if gap >= threshold and today_low > prev_close:
            results.append({
                'date': df.index[i],
                'pattern': '갭 상승',
                'detail': f'{gap*100:.1f}% 갭업 (전일종가 {prev_close:.2f} → 시가 {today_open:.2f})'
            })
    return results


def detect_gap_up_strict(df, threshold=0.05, vol_mult=1.5, close_hold=0.5, big_gap=0.10):
    """주도주 갭상승 (길 모랄레스 Buyable Gap-Up) — 미국 전용. 'Chart Pattern Analyzer' 앱 v2.3 과 같은 조건.

    조건 네 개를 모두 만족해야 한다:
      a. 갭 >= threshold
      b. 당일 거래량 >= 직전 5일 거래량 이동평균 * vol_mult
      c. 종가가 당일 봉 상단 (장중 레인지의 close_hold 이상에서 마감)
      d. 당일 저가 > 전일 종가 (갭이 메워지지 않음)

    2021~2026 S&P500 백테스트(20거래일 보유, SPY 대비, 60점 필터·시장필터 적용):
    예전 조건(갭 3%, b·c 없음) +3.50%p · 승률 48.9% (1,264건) → 이 조건 +5.37%p · 51.4% (249건).
    상위 5% 제외 시 +0.06%p vs +1.82%p 로, 차이가 소수 대박이 아니라 신호의 질에서 온다.
    한국(KOSPI+KOSDAQ)에선 낫지 않았고(+0.52 vs +0.57%p) 갭 10% 이상은 -1.85%p 라 한국은 예전 조건을 쓴다.
    """
    results = []
    close, open_, high, low, vol = df['Close'], df['Open'], df['High'], df['Low'], df['Volume']
    vma5 = vol.rolling(5).mean().shift(1)

    for i in range(6, len(df)):
        prev_close = close.iloc[i - 1]
        if prev_close <= 0:
            continue
        today_open = open_.iloc[i]
        today_low = low.iloc[i]
        gap = (today_open - prev_close) / prev_close
        if gap < threshold or today_low <= prev_close:
            continue

        base_vol = vma5.iloc[i]
        if not (base_vol > 0) or vol.iloc[i] < base_vol * vol_mult:
            continue
        vr = vol.iloc[i] / base_vol

        rng = high.iloc[i] - today_low
        if rng <= 0:
            continue
        hold = (close.iloc[i] - today_low) / rng
        if hold < close_hold:
            continue

        grade = '대형갭 ' if gap >= big_gap else ''
        results.append({
            'date': df.index[i],
            'pattern': '갭 상승',
            'detail': f'{grade}{gap*100:.1f}% 갭업 · 거래량 {vr:.1f}x · '
                      f'종가 봉상단 {hold*100:.0f}% (전일종가 {prev_close:.2f} → 시가 {today_open:.2f})'
        })
    return results


def detect_triple_bottom(df, tol=0.04, min_rise=0.08, min_gap=10, max_gap=90):
    """트리플 바텀 (미국 전용, 2026-10 사용자 요청으로 추가).

    저점 3개가 서로 tol(4%) 이내, 이웃한 저점 사이마다 min_rise(8%) 이상 반등,
    저점 간격 min_gap~max_gap 봉. 세 번째 저점 이후 넥라인(저점들 사이 최고 종가)을 돌파하면 신호.
    더블 바텀과 같은 룩어헤드 보정: 발생일 = max(돌파일, 세 번째 저점 확정일).

    2021~2026 S&P500 백테스트(20거래일, SPY 대비, 60점+시장필터): +4.30%p · 승률 51.9% (162건).
    같은 방식의 더블 바텀 +2.89%p · 51.5%, 신호 없이 60점+시장필터만 +2.47%p · 50.5%.
    평균은 낫지만 승률은 동전 던지기 수준이고 2026년(+10.05%p, 44건) 비중이 커서 근거가 약하다.
    필터 없이는 +0.16%p · 48.8%. 재현: 'Chart Pattern Analyzer' 앱 폴더 backtest/bt3.py
    """
    results = []
    close = df['Close']
    vals = close.values
    minima = find_local_minima(close, window=MIN_W)

    def chains(chain):
        if len(chain) == 3:
            yield chain
            return
        last = chain[-1]
        for nxt in minima:
            if nxt - last < min_gap:
                continue
            if nxt - last > max_gap:
                break
            lows = [vals[x] for x in chain] + [vals[nxt]]
            lo = min(lows)
            if lo <= 0 or (max(lows) - lo) / lo > tol:
                continue
            base = min(vals[last], vals[nxt])
            if (vals[last:nxt + 1].max() - base) / base < min_rise:
                continue
            yield from chains(chain + [nxt])

    for m in minima:
        for i1, i2, i3 in chains([m]):
            neck = vals[i1:i3 + 1].max()
            after = np.nonzero(vals[i3 + 1:] > neck)[0]
            if not len(after):
                continue
            # 세 번째 저점은 i3+MIN_W 이 되어야 저점으로 확정된다
            known = max(i3 + 1 + int(after[0]), i3 + MIN_W)
            if known >= len(close):
                continue
            results.append({
                'date': close.index[known],
                'pattern': '트리플 바텀',
                'detail': f'저점 {vals[i1]:.2f}/{vals[i2]:.2f}/{vals[i3]:.2f}, 넥라인 {neck:.2f} 돌파'
            })
    return results


MA_SET = (5, 20, 60, 120)   # 정배열 = 5일 > 20일 > 60일 > 120일선


def detect_second_alignment(df, min_break=3, min_first=10, max_gap=60, pre_quiet=40):
    """두 번째 정배열: 정배열 → 이탈 → 다시 정배열에 들어온 날 (미국 전용).

      a. 첫 정배열: 그 전 pre_quiet 거래일 동안 정배열이 아니었고, min_first 일 이상 유지
      b. 이탈: min_break 일 이상 정배열이 풀림 (그보다 짧은 이탈은 흔들림으로 보고 이어붙임)
      c. 재진입: 이탈 후 max_gap 거래일 안에 다시 정배열 → 이 날이 발생일 (그날 확정, 룩어헤드 없음)
    정배열은 이평선끼리의 순서만 본다 (종가 > 5일선 조건은 너무 자주 깨진다).

    2021~2026 S&P500 백테스트(20거래일, SPY 대비, 60점+시장필터): +5.02%p · 승률 57.3% (96건).
    같은 필터의 나머지 정배열 진입은 +1.46%p · 48.6%. 보유 5~60일 전부, 6년 중 5년 앞섰다(2026 제외).
    상위 5건 제외 +2.61%p, 주 단위 군집 부트스트랩 90% 구간 +2.65~+7.58%p, 기준값 16조합 전부 +3.2~+5.6%p.
    60점 필터 없이는 +0.29%p 로 엣지가 거의 없다 → 관문·점수를 거치는 이 파이프라인에서만 쓴다.
    한계: 연 15건 정도로 표본이 작고, 현재 구성종목만 쓴 생존편향이 모멘텀형 신호에 더 유리할 수 있다.
    한국은 검증하지 않았다 → scan_dataframe(us_only=True) 일 때만 돈다.
    재현: 'Chart Pattern Analyzer' 앱 폴더 backtest/bt2.py, an2.py
    """
    close = df['Close']
    mas = [close.rolling(p).mean() for p in MA_SET]
    al = pd.Series(True, index=close.index)
    for fast, slow in zip(mas, mas[1:]):
        al &= fast > slow
    al = al.fillna(False).values
    first_valid = int(mas[-1].notna().values.argmax()) - 1

    runs = []                    # 짧은 이탈은 이어붙인 정배열 구간 [시작, 끝]
    i, n = 0, len(al)
    while i < n:
        if not al[i]:
            i += 1
            continue
        j = i
        while j + 1 < n and al[j + 1]:
            j += 1
        if runs and i - runs[-1][1] - 1 < min_break:
            runs[-1][1] = j
        else:
            runs.append([i, j])
        i = j + 1

    results = []
    for k in range(1, len(runs)):
        (p_s, p_e), (s, _) = runs[k - 1], runs[k]
        # 첫 구간 앞은 120일선이 계산되기 시작한 날부터만 '정배열 아님'이 확인된다
        quiet = p_s - (runs[k - 2][1] if k >= 2 else first_valid) - 1
        gap = s - p_e - 1
        if quiet < pre_quiet or p_e - p_s + 1 < min_first or gap > max_gap:
            continue
        results.append({
            'date': df.index[s],
            'pattern': '두번째 정배열',
            'detail': f'첫 정배열 {df.index[p_s]:%m-%d}~{df.index[p_e]:%m-%d} ({p_e - p_s + 1}일) → '
                      f'{gap}일 이탈 → 재진입 {df.index[s]:%m-%d}'
        })
    return results


# V라인은 세 시장 구간 모두에서 SPY 대비 열위였으므로 기본 목록에서 제외한다.
DETECTORS = [detect_double_bottom, detect_cup_with_handle, detect_gap_up]
ALL_DETECTORS = DETECTORS + [detect_v_line]
US_ONLY_DETECTORS = [detect_second_alignment, detect_triple_bottom]   # 미국에서만 백테스트한 신호


def scan_dataframe(ticker, df, lookback_days=7, as_of=None, include_vline=False, us_only=False):
    """df 전체에서 패턴을 찾은 뒤, 발생일이 최근 lookback_days 이내인 것만 반환한다."""
    if df is None or len(df) < 60:
        return []

    if as_of is None:
        as_of = df.index[-1]
    cutoff = as_of - pd.Timedelta(days=lookback_days)

    detectors = ALL_DETECTORS if include_vline else DETECTORS
    if us_only:     # 미국: 갭상승은 강화 조건, 두번째 정배열·트리플 바텀 추가 (미국에서만 백테스트)
        detectors = [detect_gap_up_strict if d is detect_gap_up else d for d in detectors] + US_ONLY_DETECTORS
    matches = []
    seen = set()
    for detector in detectors:
        try:
            found = detector(df)
        except Exception:
            found = []
        for m in found:
            if not (cutoff <= m['date'] <= as_of):
                continue
            key = (m['pattern'], m['date'])
            if key in seen:  # 평평한 구간에서 생기는 중복 매칭 제거
                continue
            seen.add(key)
            m['ticker'] = ticker
            m['price'] = float(df['Close'].iloc[-1])
            matches.append(m)
    return matches
