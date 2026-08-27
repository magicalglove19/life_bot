"""스크리너 설정값.

숫자는 전부 여기서 바꾼다. 미너비니의 원문 기준을 기본값으로 두되,
시장 상황에 따라 조일 수 있도록 전부 파라미터화했다.
"""

from dataclasses import dataclass, field


@dataclass
class TrendTemplateConfig:
    """『Trade Like a Stock Market Wizard』의 8개 Trend Template 기준."""

    # 이동평균 (미너비니는 EMA가 아니라 SMA를 쓴다)
    ma_short: int = 50
    ma_mid: int = 150
    ma_long: int = 200

    # 200일선이 "상승 중"인지 판단하는 룩백 (책은 최소 1개월, 4~5개월이면 더 좋다)
    ma_long_slope_lookback: int = 20

    # 52주 = 거래일 기준 약 252봉
    week52_bars: int = 252

    min_pct_above_52w_low: float = 30.0   # 52주 저점 대비 최소 +30%
    max_pct_below_52w_high: float = 25.0  # 52주 고점 대비 -25% 이내
    min_rs_rating: float = 70.0           # RS 등급 70 이상 (80~90대 선호)


@dataclass
class RSConfig:
    """IBD 스타일 상대강도(RS) 점수 가중치."""

    periods: tuple = (63, 126, 189, 252)
    weights: tuple = (0.4, 0.2, 0.2, 0.2)


@dataclass
class VCPConfig:
    """Volatility Contraction Pattern 탐지 파라미터."""

    lookback: int = 90          # 베이스를 찾을 최대 구간(거래일)
    min_base_bars: int = 12     # 너무 짧은 베이스는 제외
    swing_order: int = 3        # 스윙 고/저점 프랙탈 폭 (좌우 N봉)

    min_contractions: int = 2   # 최소 수축 횟수 (2~6이 정상)
    max_contractions: int = 6

    max_first_depth: float = 35.0   # 첫 수축(가장 깊은 조정)의 최대 깊이 %
    max_last_depth: float = 12.0    # 마지막 수축은 타이트해야 한다 %
    contraction_ratio: float = 0.90  # 다음 수축은 직전의 90% 이하

    # 거래량 마름(volume dry-up): 최근 5일 평균 / 50일 평균
    max_dryup_ratio: float = 0.85
    require_dryup: bool = False  # True면 거래량 마름을 하드 필터로 적용

    # 피벗(매수 트리거)까지의 거리 %
    max_distance_to_pivot: float = 6.0

    # 돌파 확인용 거래량 배수 (50일 평균 대비)
    breakout_volume_mult: float = 1.4

    # 피벗(매수 타점)을 넘은 지 이 거래일 수를 넘기면 '연장(extended)' — 쫓아가는 매수가 된다
    max_days_past_pivot: int = 5

    max_stop_distance: float = 10.0  # 피벗 진입 기준 구조적 손절폭 상한 %


@dataclass
class FundamentalConfig:
    """SEPA 펀더멘털 (Code 33) 파라미터."""

    quarters_required: int = 3     # 3분기 연속 가속
    min_eps_growth: float = 20.0   # YoY EPS 성장률 최소 %
    min_sales_growth: float = 10.0  # YoY 매출 성장률 최소 %


@dataclass
class RiskConfig:
    """미너비니식 리스크 관리."""

    account_size: float = 100_000.0
    risk_per_trade_pct: float = 1.25   # 계좌의 1.25~2.5%
    max_stop_pct: float = 8.0          # 최대 손절폭 7~8%
    max_position_pct: float = 25.0     # 한 종목 최대 비중


@dataclass
class Config:
    trend: TrendTemplateConfig = field(default_factory=TrendTemplateConfig)
    rs: RSConfig = field(default_factory=RSConfig)
    vcp: VCPConfig = field(default_factory=VCPConfig)
    fundamental: FundamentalConfig = field(default_factory=FundamentalConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    benchmark: str = "SPY"
    history_days: int = 500        # 252봉 RS + 200일선을 위해 넉넉히
    min_price: float = 5.0         # 저가주 제외
    min_avg_dollar_volume: float = 5_000_000.0  # 유동성 필터 (50일 평균 거래대금)
