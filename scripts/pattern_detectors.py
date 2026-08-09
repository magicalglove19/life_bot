# -*- coding: utf-8 -*-
"""차트 패턴 감지 로직 (Chart Pattern Analyzer 앱과 동일 기준).
- Cup with Handle (컵위드핸들)
- Double Bottom (더블 바텀)
- V-Line (V자 반등)
- Gap Up (갭 상승)
chart_patterns.py에서 import해서 사용.
"""
import pandas as pd
import numpy as np


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
    minima_idx = find_local_minima(close, window=5)
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
            diff_pct = abs(low1 - low2) / low1
            if diff_pct > 0.04:
                continue

            between = close.iloc[i1:i2 + 1]
            peak = between.max()
            base = min(low1, low2)
            rise_pct = (peak - base) / base
            if rise_pct < 0.08:
                continue

            after = close.iloc[i2 + 1:]
            breakout_date = None
            for k in range(len(after)):
                if after.iloc[k] > peak:
                    breakout_date = after.index[k]
                    break
            if breakout_date is not None:
                results.append({
                    'date': breakout_date,
                    'pattern': '더블 바텀',
                    'detail': f'저점 {low1:.2f}/{low2:.2f} (차이 {diff_pct*100:.1f}%), 넥라인 {peak:.2f} 돌파'
                })
    return results


def detect_cup_with_handle(df):
    """왼쪽 고점(left rim) -> 12~35% 하락한 둥근 바닥(cup bottom) ->
    왼쪽과 15% 이내인 오른쪽 고점(right rim) -> 오른쪽 고점 대비 3~15% 조정(handle)
    -> 오른쪽 고점 돌파, 순서로 이어지는 지점을 찾는다. 돌파일을 발생일로 본다."""
    results = []
    close = df['Close']
    n = len(close)

    maxima = find_local_maxima(close, window=5)
    minima = find_local_minima(close, window=5)
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
                    breakout_date = None
                    for k in range(len(after)):
                        if after.iloc[k] > right_rim:
                            breakout_date = after.index[k]
                            break
                    if breakout_date is not None:
                        results.append({
                            'date': breakout_date,
                            'pattern': '컵위드핸들',
                            'detail': f'컵 깊이 {depth*100:.1f}%, 핸들 조정 {pullback*100:.1f}%, 돌파가 {right_rim:.2f}'
                        })
    return results


def detect_v_line(df, decline_days=10, drop_pct=0.08, rally_days=10, rally_pct=0.08):
    """짧은 기간(최대 10거래일) 안에 8% 이상 급락한 뒤,
    이후 10거래일 안에 다시 8% 이상 급반등하는 뾰족한 V자 저점을 찾는다."""
    results = []
    close = df['Close']
    n = len(close)
    minima_idx = find_local_minima(close, window=3)

    for idx in minima_idx:
        if idx < decline_days or idx > n - 2:
            continue
        pre_high = close.iloc[max(0, idx - decline_days):idx].max()
        low = close.iloc[idx]
        if pre_high <= 0:
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
        conf_date = post_window.index[rally_pos]
        if conf_date == df.index[idx]:
            continue
        results.append({
            'date': conf_date,
            'pattern': 'V라인',
            'detail': f'{decline*100:.1f}% 급락 후 {rally*100:.1f}% 급반등 (저점 {low:.2f})'
        })
    return results


def detect_gap_up(df, threshold=0.03):
    """전일 종가 대비 시가가 threshold 이상 갭으로 뜨고, 당일 저가가
    전일 종가를 채우지 않은 경우(갭이 메워지지 않음)를 감지한다."""
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


DETECTORS = [detect_double_bottom, detect_cup_with_handle, detect_v_line, detect_gap_up]


def scan_dataframe(ticker, df, lookback_days=7, as_of=None):
    """df 전체에서 패턴을 찾은 뒤, 발생일이 최근 lookback_days 이내인 것만 반환한다."""
    if df is None or len(df) < 60:
        return []

    if as_of is None:
        as_of = df.index[-1]
    cutoff = as_of - pd.Timedelta(days=lookback_days)

    matches = []
    seen = set()
    for detector in DETECTORS:
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
