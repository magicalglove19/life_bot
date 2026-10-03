"""박스(유동성 구간)·POC(중앙선)·델타 계산."""
import numpy as np
import pandas as pd


def delta_proxy(df: pd.DataFrame, kind: str = "clv") -> pd.Series:
    """봉 하나의 매수-매도 체결량 추정.

    clv : 종가가 봉 안에서 어디에 닫혔나 (고가 마감=전량 매수, 저가 마감=전량 매도)
    body: 몸통 길이 / 봉 길이
    """
    rng = (df["High"] - df["Low"]).replace(0, np.nan)
    if kind == "clv":
        frac = (2 * df["Close"] - df["High"] - df["Low"]) / rng
    else:
        frac = (df["Close"] - df["Open"]) / rng
    return (frac.fillna(0) * df["Volume"]).rename("delta")


def slot_mean_volume(df: pd.DataFrame, days: int = 20) -> pd.Series:
    """같은 시간대(예: 10시 봉끼리)의 직전 N일 평균 거래량.

    장 시작 첫 봉은 늘 거래량이 몰리므로, 시간대가 다른 봉끼리 비교하면
    첫 봉이 항상 '폭발'로 보인다.
    """
    slot = df.index.strftime("%H:%M")
    return (df["Volume"].groupby(slot)
            .transform(lambda s: s.shift(1).rolling(days, min_periods=5).mean()))


def daily_atr(df: pd.DataFrame, n: int = 20) -> pd.Series:
    """1시간봉을 일봉으로 묶어 ATR 계산 후, 각 1시간봉에 '전일까지의' 값을 붙인다."""
    d = df.groupby(df.index.date).agg(High=("High", "max"), Low=("Low", "min"),
                                       Close=("Close", "last"))
    pc = d["Close"].shift(1)
    tr = pd.concat([d["High"] - d["Low"], (d["High"] - pc).abs(),
                    (d["Low"] - pc).abs()], axis=1).max(axis=1)
    atr = tr.rolling(n, min_periods=10).mean().shift(1)
    return pd.Series(atr.reindex(df.index.date).values, index=df.index)


def profile(high, low, vol, bins: int = 40):
    """볼륨 프로파일. 각 봉의 거래량을 고가~저가에 고르게 뿌린다.

    반환: (POC, 가격구간 경계 배열, 구간별 거래량)
    """
    lo, hi = low.min(), high.max()
    if hi <= lo:
        return lo, None, None
    edges = np.linspace(lo, hi, bins + 1)
    centers = (edges[:-1] + edges[1:]) / 2
    hist = np.zeros(bins)
    for h, l, v in zip(high, low, vol):
        mask = (centers >= l) & (centers <= h)
        n = mask.sum()
        if n == 0:
            hist[np.clip(np.searchsorted(edges, (h + l) / 2) - 1, 0, bins - 1)] += v
        else:
            hist[mask] += v / n
    return centers[hist.argmax()], edges, hist


def rolling_boxes(df: pd.DataFrame, n_bars: int) -> pd.DataFrame:
    """각 봉 시점에서 '직전 n_bars 개 봉'으로 만든 박스.

    현재 봉은 포함하지 않는다 (현재 봉이 박스를 뚫었는지 판정해야 하므로).
    box_hi/box_lo : 구간 최고가/최저가
    poc           : 거래량 최대 가격 (중앙선)
    poc_cross     : 그 구간에서 종가가 POC 를 위아래로 건넌 횟수 (균형 여부)
    """
    H, L, C, V = (df[c].values for c in ("High", "Low", "Close", "Volume"))
    out = np.full((len(df), 4), np.nan)
    for i in range(n_bars, len(df)):
        s = slice(i - n_bars, i)
        poc, _, _ = profile(H[s], L[s], V[s])
        side = np.sign(C[s] - poc)
        side = side[side != 0]
        cross = (np.diff(side) != 0).sum() if len(side) > 1 else 0
        out[i] = (H[s].max(), L[s].min(), poc, cross)
    return pd.DataFrame(out, index=df.index, columns=["box_hi", "box_lo", "poc", "poc_cross"])
