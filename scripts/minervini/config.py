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
    # RS 등급 하한. 미너비니는 최소 70, 80~90대를 선호한다고 쓴다.
    # review.py 로 최근 6개월을 되감아 재보니 이 숫자를 올릴수록 성적이 단조 증가했다.
    #   RS 70 → '강력매수'(오늘 돌파) 59건 · 월 9.8건 · SPY 대비 +0.32%
    #   RS 85 → 35건 · 월 5.8건 · 승률 62% · 손익비 1.64 · SPY 대비 +2.14%  ← 현재값
    #   RS 90 → 26건 · 월 4.3건 · 손익비 1.98 · SPY 대비 +2.70% (표본이 얇다)
    # 85를 고른 이유는 표본이 35건으로 더 두툼해 결과를 믿을 만해서다.
    # 다시 재보려면: python3 review.py --compare
    min_rs_rating: float = 85.0


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

    # 돌파 확인용 거래량 배수 (50일 평균 대비).
    # 미너비니 원문은 1.4배 정도지만, review.py 로 1년치를 재보니 거래량이 셀수록 성적이 단조 증가했다.
    #   돌파 거래량 없음 331건 → SPY 대비 -0.87%
    #   1.4~2.0배      62건 → +0.48%
    #   2.0~3.0배      30건 → +2.53% (승률 60%)
    #   3.0배+         21건 → +3.43%
    # 하한을 2.0으로 올리면 '강력매수'가 월 8.2건 → 3.8건으로 줄지만
    # SPY 대비 +2.20% → +3.49%, 승률 61% → 64%, 손익비 1.55 → 1.74 로 나아진다.
    # 다시 재보려면: python3 review.py --compare --months 12
    breakout_volume_mult: float = 2.0

    # 피벗(매수 타점)을 넘은 지 이 거래일 수를 넘기면 '연장(extended)' — 쫓아가는 매수가 된다
    max_days_past_pivot: int = 5

    # 피벗 위로 이 %를 넘게 올라가 있으면 날짜와 상관없이 '연장'.
    # 오늘 돌파했어도 피벗 위 10%에서 사면 손절선이 멀어 쫓아가는 매수가 된다.
    max_pct_above_pivot: float = 5.0

    max_stop_distance: float = 10.0  # 피벗 진입 기준 구조적 손절폭 상한 %

    # --- 돌파 종목 전용 점수 (vcp._breakout_score) ---
    # 기존 _score()는 '돌파 전 셋업'을 줄 세우려고 만든 함수인데, 돌파한 종목에도
    # 같은 점수가 쓰이고 있었다. 2022/2023/2025~26 세 구간에서 VCP 돌파 8,129건을
    # 재생해 재보니 100점 중 40점이 세 구간 모두 역방향이었다.
    #   거래량 마름 20점 : 최근 5일 평균에 '돌파일'이 포함돼, 확인 신호인 거래량
    #                      급증에 벌점을 준다 (dryup ~ 돌파거래량 상관 +0.57~+0.65)
    #   스프레드 압축 10점: 같은 이유로 돌파일의 레인지 확대에 벌점
    #   피벗 근접도  10점 : 피벗에 붙어 있을수록 만점인데, 돌파일 기준 피벗 대비
    #                      중앙값이 0.73~0.92%라 10일 내 되밀림이 거의 자동이었다
    # 실제로 현행 점수 상위 25%가 하위 25%보다 휩소가 +4.4~+10.6%p 더 많았다.
    # 아래 값은 같은 데이터에서 잰 단조 곡선을 그대로 옮긴 것이다. 검증 결과
    # 상위 25%의 휩소가 하위 25% 대비 -29%p, 초과수익 +0.85~+1.50%p (3구간 일치).
    bo_score_vol_pts: float = 35.0      # 돌파일 거래량 배수 배점
    bo_score_vol_cap: float = 3.0       # 50일 평균의 3배에서 만점
    bo_score_room_pts: float = 30.0     # 피벗 위 여유 배점
    bo_score_room_min: float = 0.3      # 피벗 위 0.3%부터 점수 시작
    bo_score_room_cap: float = 4.0      # 피벗 위 4%에서 만점
    bo_score_count_pts: float = 15.0    # 수축 횟수 배점
    bo_score_decay_pts: float = 20.0    # 수축 감쇠비 배점 (일관되게 유효했음)


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

    # 자리에 따라 거는 리스크를 다르게 한다 — 미너비니도 확신도에 따라 비중을 조절한다.
    # review.py 로 1년치를 재본 결과가 근거다.
    #   거래량 확인된 '돌파'  39건 · 승률 64% · 손익비 1.74 · SPY 대비 +3.49%
    #   아직 안 터진 '매수구간' 357건 · 승률 42% · 손익비 1.18 · SPY 대비 -0.92%
    # 앞의 자리에 1회 리스크를 다 걸고, 뒤의 자리는 절반만 건다.
    # 1.0 / 1.0 으로 두면 예전처럼 똑같이 걸린다.
    breakout_risk_mult: float = 1.0   # 돌파 — 거래량으로 확인된 자리
    setup_risk_mult: float = 0.5      # 매수구간·형성중 — 아직 확인 안 된 자리


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
