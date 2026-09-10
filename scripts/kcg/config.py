"""스크리너 설정값.

숫자는 전부 여기서 바꾼다. 강창권 트레이더의 원문 기준을 기본값으로 두되,
종목·시장 성격에 따라 조일 수 있도록 전부 파라미터화했다.
"""

from dataclasses import dataclass, field


@dataclass
class TriggerConfig:
    """추적을 시작하는 '사건' — 상한가 / 장대양봉 / 신고가."""

    limit_up_pct: float = 29.0        # 상한가 판정 (전일 종가 대비 %, 국내 제한폭 30%)
    big_body_pct: float = 12.0        # 장대양봉 최소 몸통 (시가 대비 %)
    big_body_ratio: float = 0.55      # 몸통 / 캔들 전체 길이 — 위아래 꼬리가 길면 제외
    big_vol_mult: float = 2.0         # 장대양봉 당일 거래량 / 20일 평균

    # 갭상승으로 크게 오른 날은 몸통이 작아 장대양봉에 안 걸린다.
    # 등락률 기준으로도 급등을 잡는다 (세력 흔적은 몸통이 아니라 상승폭에 남는다).
    surge_pct: float = 15.0           # 전일 종가 대비 등락률
    surge_vol_mult: float = 2.0       # 급등일 거래량 / 20일 평균

    new_high_bars: int = 252          # 52주 신고가 (거래일 기준)
    high_lookbacks: tuple = (5, 20, 252)   # 신고가 표기용 (5일/20일/52주)

    max_age: int = 15                 # 트리거 이후 추적하는 최대 거래일 (패턴 C 12봉 + 여유)


@dataclass
class PatternAConfig:
    """패턴 A — 2~3일 단기 조정 (장대양봉 상단 도지)."""

    min_bars: int = 2                 # 트리거 다음날부터 센 조정 봉 수
    max_bars: int = 3

    ma_support: int = 5               # 이 이동평균선 위에서 버텨야 한다
    ma_tolerance: float = 0.0         # 원문은 "5일선을 깨지 않고" — 허용치 없음

    max_vol_vs_trigger: float = 0.50  # 조정 구간 평균 거래량 / 트리거일 거래량
    require_vol_decline: bool = True  # 조정 내내 거래량이 매일 줄어야 함 (반드시 감소)

    max_drop_from_high: float = 5.0   # 종가가 장대양봉 고가 대비 -5% 이내 ("상단 5% 이내")
    max_doji_body: float = 2.0        # 오늘 캔들 몸통 (|종가-시가|/시가 %) 상한 = 도지
    max_range_ratio: float = 0.35     # 오늘 캔들 길이 / 트리거 캔들 길이


@dataclass
class PatternBConfig:
    """패턴 B — 일주일 조정 (4음 1양)."""

    min_bars: int = 5                 # 음봉 4개 + 반등 양봉 = 최소 5거래일
    max_bars: int = 6

    min_down_bars: int = 4            # 원문 그대로 음봉 4개
    require_consecutive: bool = True  # 음봉이 연속으로 나와야 한다 (중간에 양봉이 끼면 탈락)
    require_real_decline: bool = True # 종가가 전일보다 낮은, 실제로 밀린 봉만 음봉으로 센다
    require_ma5_break: bool = True    # 조정 중 5일선을 실제로 깼는지

    max_vol_vs_trigger: float = 0.30  # 4일간 거래량 급감

    ma_support: int = 20              # 20일선에서 지지받는 양봉
    ma_tolerance: float = 0.0         # 원문은 "20일선 근처/위" — 종가는 20일선 위여야 한다
    max_ma_undercut: float = 3.0      # 조정 저점이 20일선을 이만큼(%) 넘게 깨면 탈락


@dataclass
class PatternCConfig:
    """패턴 C — 2주 기간 조정 (캔들 8~12개)."""

    min_bars: int = 8
    max_bars: int = 12

    max_vol_vs_trigger: float = 0.25  # 조정 구간 거래량이 극도로 줄어야 한다
    max_dryup_ratio: float = 0.60     # 조정 중 (5일 평균 거래량 / 20일 평균) 최저치
    vol_revive_mult: float = 2.00     # 오늘 거래량 / 조정 구간 평균 — 재증가 확인

    ma_reclaim: int = 5               # 다시 올라타야 하는 이동평균선
    ma_hold: int = 20                 # 홀딩 기준선
    max_ma_undercut: float = 5.0      # 조정 저점이 20일선을 깨도 되는 한계 %
    max_depth: float = 20.0           # 트리거 종가 대비 조정 저점 하락률 상한 %


@dataclass
class ExitConfig:
    """매도 타점 및 리스크 관리."""

    blowoff_body_pct: float = 12.0    # 장대양봉 2개 연속 → 단기 꼭지
    blowoff_2day_gain: float = 50.0   # 이틀 누적 상승률 % → 시세 분출

    stop_ma_fast: int = 5             # 패턴 A 손절선
    stop_ma_slow: int = 20            # 패턴 B/C 손절·홀딩선

    reentry_ma: int = 10              # 세력주 재진입: 10일선 지지 확인
    reentry_tolerance: float = 2.0    # 저가가 10일선을 밑도는 허용치 %
    reentry_lookback: int = 7         # 5일선 이탈 이후 재진입을 노리는 기간 (거래일)


@dataclass
class RiskConfig:
    """포트폴리오 제약과 추격매수 경고."""

    max_positions: int = 5            # 최대 보유 종목 수 (권장 3~5)
    ideal_positions: int = 3

    chase_day_gain: float = 15.0      # 오늘 상승률이 이만큼 넘는데 사려 하면 추격매수
    chase_ma5_gap: float = 12.0       # 5일선 이격도 % 상한
    chase_ma20_gap: float = 25.0      # 20일선 이격도 % 상한

    scout_shares: int = 1             # 추적 관찰용 정찰주 수량


@dataclass
class Config:
    trigger: TriggerConfig = field(default_factory=TriggerConfig)
    pattern_a: PatternAConfig = field(default_factory=PatternAConfig)
    pattern_b: PatternBConfig = field(default_factory=PatternBConfig)
    pattern_c: PatternCConfig = field(default_factory=PatternCConfig)
    exit: ExitConfig = field(default_factory=ExitConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)

    history_days: int = 500           # 52주 신고가 판정을 위해 넉넉히 (달력일)
    min_price: int = 1_000            # 동전주 제외 (원)
    min_avg_trade_value: float = 1_000_000_000.0   # 20일 평균 거래대금 10억 미만 제외

    market: str = "ALL"               # ALL / KOSPI / KOSDAQ
    exclude_spac: bool = True         # 스팩 제외
    exclude_preferred: bool = True    # 우선주 제외

    min_score: float = 60.0           # 시그널 점수 하한
