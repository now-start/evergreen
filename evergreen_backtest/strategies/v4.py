"""V4: 일봉 레짐/ATR 손절에 주간 EMA 추세 필터를 추가한 전략."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import math
import os

from evergreen_backtest.contracts import SignalAction, resolve_action, resolve_target_position_ratio

MIN_EQUITY = 1e-12
FULL_POSITION_UNITS = 3


class MarketRegime(str, Enum):
    BULL = "BULL"
    BEAR = "BEAR"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class CandleBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class StrategyParamsV4:
    fee_per_side: float
    slippage: float
    regime_ema_len: int
    atr_period: int
    atr_trail_multiplier: float
    regime_band: float
    weekly_ema_len: int


@dataclass(frozen=True)
class BacktestRowV4:
    timestamp: datetime
    open: float
    close: float
    ma: float
    weekly_close: float
    weekly_ema: float
    weekly_bullish: bool
    rsi: float
    action: str
    signal_reason: str
    target_position_ratio: float
    setup_buy: bool
    setup_sell: bool
    trail_stop_triggered: bool
    regime: str
    regime_anchor: float
    regime_upper: float
    regime_lower: float
    atr_trail_stop: float
    pos_open: float
    ret_oo: float
    equity: float
    equity_bh: float
    trade: float


@dataclass(frozen=True)
class BacktestSummary:
    final_equity: float
    final_equity_bh: float
    cagr: float
    mdd: float
    trades: int
    range: str


@dataclass(frozen=True)
class BacktestResultV4:
    rows: list[BacktestRowV4]
    summary: BacktestSummary


@dataclass(frozen=True)
class GridSearchRowV4:
    params: StrategyParamsV4
    calmar_like: float
    cagr: float
    mdd: float
    final_equity: float


@dataclass(frozen=True)
class BacktestConfigV4:
    enabled: bool = True
    validation_ratio: float = 0.7
    fee_per_side: float = 0.0005
    slippage: float = 0.0002
    top_k: int = 10
    grid_parallelism: int = max(1, os.cpu_count() or 1)
    grid_ma_len_range: str = "200:200:1"
    grid_atr_period_range: str = "14:14:1"
    grid_atr_trail_mult_range: str = "3:3:1"
    grid_regime_band_range: str = "0.02:0.02:0.01"
    grid_weekly_ema_len_range: str = "20:20:1"

    def __post_init__(self) -> None:
        if not (0.0 < self.validation_ratio < 1.0):
            raise ValueError("validation-ratio must be between 0 and 1")
        if self.fee_per_side < 0 or self.slippage < 0:
            raise ValueError("fee-per-side and slippage must be >= 0")
        if self.top_k <= 0:
            raise ValueError("top-k must be > 0")
        if self.grid_parallelism <= 0:
            raise ValueError("grid-parallelism must be > 0")

    def resolve_ma_len_values(self) -> list[int]:
        return _parse_positive_int_range(self.grid_ma_len_range, "grid-ma-len-range")

    def resolve_atr_period_values(self) -> list[int]:
        return _parse_positive_int_range(self.grid_atr_period_range, "grid-atr-period-range")

    def resolve_atr_trail_multipliers(self) -> list[float]:
        values = _parse_double_range(self.grid_atr_trail_mult_range)
        for value in values:
            if value < 0.0:
                raise ValueError("grid-atr-trail-mult-range must be >= 0")
        return values

    def resolve_regime_band_values(self) -> list[float]:
        values = _parse_double_range(self.grid_regime_band_range)
        for value in values:
            if value < 0.0 or value >= 1.0:
                raise ValueError("grid-regime-band-range must be in [0, 1)")
        return values

    def resolve_weekly_ema_len_values(self) -> list[int]:
        return _parse_positive_int_range(self.grid_weekly_ema_len_range, "grid-weekly-ema-len-range")

    def combination_count(self) -> int:
        total = 1
        for size in (
            len(self.resolve_ma_len_values()),
            len(self.resolve_atr_period_values()),
            len(self.resolve_atr_trail_multipliers()),
            len(self.resolve_regime_band_values()),
            len(self.resolve_weekly_ema_len_values()),
        ):
            if size <= 0:
                raise ValueError("grid axis must not be empty")
            total *= size
        return total

    def validation_split_index(self, total_bars: int) -> int:
        if total_bars < 4:
            raise ValueError("At least 4 bars are required for validation/test split")
        split = int(math.floor(total_bars * self.validation_ratio))
        split = max(2, split)
        split = min(total_bars - 2, split)
        return split


class BacktestServiceV4:
    def backtest_daily_strategy(self, daily_bars: list[CandleBar], params: StrategyParamsV4) -> BacktestResultV4:
        if daily_bars is None or len(daily_bars) < 2:
            raise ValueError("At least 2 daily bars are required")
        self._validate_params(params)

        n = len(daily_bars)
        open_ = [bar.open for bar in daily_bars]
        high = [bar.high for bar in daily_bars]
        low = [bar.low for bar in daily_bars]
        close = [bar.close for bar in daily_bars]

        regime_ema = self._exponential_moving_average(close, params.regime_ema_len)
        atr = self._wilder_atr(high, low, close, params.atr_period)
        regimes, anchor, upper, lower = self._resolve_regimes(close, regime_ema, params.regime_band)
        weekly_close, weekly_ema, weekly_bullish = self._build_weekly_filter(daily_bars, params.weekly_ema_len)

        should_buy = [False] * n
        should_sell = [False] * n
        setup_buy = [False] * n
        setup_sell = [False] * n
        trail_stop_triggered = [False] * n
        atr_trail_stop = [math.nan] * n
        pos_close_units = [0] * n
        pos_open_units = [0] * n
        pos_open_exposure = [0.0] * n
        ret_oo = [0.0] * n
        trade = [0.0] * n
        equity = [0.0] * n
        equity_bh = [0.0] * n

        for i in range(n - 1):
            ret_oo[i] = (open_[i + 1] / open_[i]) - 1.0

        highest_close_since_entry = math.nan
        for i in range(n):
            pos_open_units[i] = 0 if i == 0 else pos_close_units[i - 1]
            pos_open_exposure[i] = pos_open_units[i] / float(FULL_POSITION_UNITS)
            trade[i] = abs(pos_open_exposure[i]) if i == 0 else abs(pos_open_exposure[i] - pos_open_exposure[i - 1])

            previous_open_units = 0 if i == 0 else pos_open_units[i - 1]
            current_open_units = pos_open_units[i]
            if current_open_units > previous_open_units:
                highest_close_since_entry = close[i]
            elif current_open_units > 0:
                highest_close_since_entry = max(highest_close_since_entry, close[i]) if math.isfinite(highest_close_since_entry) else close[i]
            else:
                highest_close_since_entry = math.nan

            buy_setup = self._is_buy_setup(i, regimes) and weekly_bullish[i]
            sell_setup = self._is_sell_setup(i, regimes, current_open_units)
            trail_stop = self._evaluate_atr_trail_stop(
                i,
                close,
                atr,
                highest_close_since_entry,
                current_open_units,
                params,
            )

            target_units = current_open_units
            if trail_stop[1] or sell_setup:
                target_units = 0
            elif buy_setup:
                target_units = FULL_POSITION_UNITS

            setup_buy[i] = buy_setup
            setup_sell[i] = sell_setup
            trail_stop_triggered[i] = trail_stop[1]
            atr_trail_stop[i] = trail_stop[0]
            should_buy[i] = target_units > current_open_units
            should_sell[i] = target_units < current_open_units
            pos_close_units[i] = target_units

        cost_unit = params.fee_per_side + params.slippage
        equity[0] = 1.0
        equity_bh[0] = max(MIN_EQUITY, 1.0 - cost_unit)

        for i in range(1, n):
            turnover_cost = trade[i] * cost_unit
            gross = 1.0 + (pos_open_exposure[i] * ret_oo[i]) - turnover_cost
            equity[i] = MIN_EQUITY if (not math.isfinite(gross) or gross <= 0.0) else max(MIN_EQUITY, equity[i - 1] * gross)

            bh_gross = 1.0 + ret_oo[i]
            equity_bh[i] = MIN_EQUITY if (not math.isfinite(bh_gross) or bh_gross <= 0.0) else max(MIN_EQUITY, equity_bh[i - 1] * bh_gross)

        final_equity = equity[-1]
        final_equity_bh = equity_bh[-1]
        years = max(1.0 / 365.25, ((daily_bars[-1].timestamp - daily_bars[0].timestamp).days / 365.25))
        cagr = math.pow(final_equity, 1.0 / years) - 1.0
        mdd = self._max_drawdown(equity)
        trades = self._count_trade_executions(trade)
        actions = [resolve_action(should_buy[i], should_sell[i]) for i in range(n)]

        rows = [
            BacktestRowV4(
                timestamp=daily_bars[i].timestamp,
                open=open_[i],
                close=close[i],
                ma=regime_ema[i],
                weekly_close=weekly_close[i],
                weekly_ema=weekly_ema[i],
                weekly_bullish=weekly_bullish[i],
                rsi=math.nan,
                action=actions[i].value,
                signal_reason=self._resolve_signal_reason(
                    actions[i],
                    setup_buy[i],
                    setup_sell[i],
                    trail_stop_triggered[i],
                ),
                target_position_ratio=resolve_target_position_ratio(
                    actions[i],
                    pos_open_exposure[i],
                ),
                setup_buy=setup_buy[i],
                setup_sell=setup_sell[i],
                trail_stop_triggered=trail_stop_triggered[i],
                regime=regimes[i].value,
                regime_anchor=anchor[i],
                regime_upper=upper[i],
                regime_lower=lower[i],
                atr_trail_stop=atr_trail_stop[i],
                pos_open=pos_open_exposure[i],
                ret_oo=ret_oo[i],
                equity=equity[i],
                equity_bh=equity_bh[i],
                trade=trade[i],
            )
            for i in range(n)
        ]
        summary = BacktestSummary(
            final_equity=final_equity,
            final_equity_bh=final_equity_bh,
            cagr=cagr,
            mdd=mdd,
            trades=trades,
            range=f"{daily_bars[0].timestamp} -> {daily_bars[-1].timestamp}",
        )
        return BacktestResultV4(rows=rows, summary=summary)

    @staticmethod
    def _is_buy_setup(index: int, regimes: list[MarketRegime]) -> bool:
        if index <= 0:
            return False
        return regimes[index - 1] == MarketRegime.BEAR and regimes[index] == MarketRegime.BULL

    @staticmethod
    def _is_sell_setup(index: int, regimes: list[MarketRegime], current_open_units: int) -> bool:
        if current_open_units <= 0 or index <= 0:
            return False
        return regimes[index - 1] == MarketRegime.BULL and regimes[index] == MarketRegime.BEAR

    @staticmethod
    def _evaluate_atr_trail_stop(
        index: int,
        close: list[float],
        atr: list[float],
        highest_close_since_entry: float,
        current_open_units: int,
        params: StrategyParamsV4,
    ) -> tuple[float, bool]:
        if params.atr_trail_multiplier <= 0.0 or current_open_units <= 0:
            return (math.nan, False)
        if not math.isfinite(atr[index]) or not math.isfinite(highest_close_since_entry):
            return (math.nan, False)
        stop = highest_close_since_entry - (params.atr_trail_multiplier * atr[index])
        return (stop, close[index] <= stop)

    @staticmethod
    def _resolve_regimes(close: list[float], anchor: list[float], regime_band: float) -> tuple[list[MarketRegime], list[float], list[float], list[float]]:
        n = len(close)
        regimes = [MarketRegime.UNKNOWN] * n
        upper = [math.nan] * n
        lower = [math.nan] * n

        for i in range(n):
            if not math.isfinite(anchor[i]):
                regimes[i] = MarketRegime.UNKNOWN
                continue

            upper[i] = anchor[i] * (1.0 + regime_band)
            lower[i] = anchor[i] * (1.0 - regime_band)

            previous = MarketRegime.UNKNOWN if i == 0 else regimes[i - 1]
            if close[i] > upper[i]:
                regimes[i] = MarketRegime.BULL
            elif close[i] < lower[i]:
                regimes[i] = MarketRegime.BEAR
            elif previous != MarketRegime.UNKNOWN:
                regimes[i] = previous
            elif close[i] > anchor[i]:
                regimes[i] = MarketRegime.BULL
            elif close[i] < anchor[i]:
                regimes[i] = MarketRegime.BEAR
            else:
                regimes[i] = MarketRegime.UNKNOWN

        return (regimes, anchor, upper, lower)

    def _build_weekly_filter(self, daily_bars: list[CandleBar], weekly_ema_len: int) -> tuple[list[float], list[float], list[bool]]:
        n = len(daily_bars)
        week_keys: list[tuple[int, int]] = []
        weekly_closes: list[float] = []
        weekly_close_by_day = [math.nan] * n
        weekly_ema_by_day = [math.nan] * n
        weekly_bull_by_day = [False] * n

        for i, bar in enumerate(daily_bars):
            iso = bar.timestamp.isocalendar()
            key = (iso.year, iso.week)
            if not week_keys or week_keys[-1] != key:
                week_keys.append(key)
                weekly_closes.append(bar.close)
            else:
                weekly_closes[-1] = bar.close

            weekly_ema_series = self._exponential_moving_average(weekly_closes, weekly_ema_len)
            weekly_close_by_day[i] = weekly_closes[-1]
            weekly_ema_by_day[i] = weekly_ema_series[-1]
            weekly_bull_by_day[i] = math.isfinite(weekly_ema_series[-1]) and weekly_closes[-1] >= weekly_ema_series[-1]

        return (weekly_close_by_day, weekly_ema_by_day, weekly_bull_by_day)

    @staticmethod
    def _resolve_signal_reason(
        action: SignalAction,
        setup_buy: bool,
        setup_sell: bool,
        trail_stop_triggered: bool,
    ) -> str:
        if action == SignalAction.BUY:
            return "BUY_REGIME_WEEKLY_FILTER"
        if action == SignalAction.SELL and setup_sell and trail_stop_triggered:
            return "SELL_REGIME_AND_TRAIL_STOP"
        if action == SignalAction.SELL and trail_stop_triggered:
            return "SELL_TRAIL_STOP"
        if action == SignalAction.SELL:
            return "SELL_REGIME_TRANSITION"
        if setup_buy:
            return "SETUP_BUY"
        if setup_sell:
            return "SETUP_SELL"
        return "NONE"

    @staticmethod
    def _validate_params(params: StrategyParamsV4) -> None:
        if params is None:
            raise ValueError("strategy params are required")
        if params.fee_per_side < 0 or params.slippage < 0:
            raise ValueError("fee/slippage must be >= 0")
        if params.regime_ema_len <= 0:
            raise ValueError("regime-ema-len must be > 0")
        if params.weekly_ema_len <= 0:
            raise ValueError("weekly-ema-len must be > 0")
        if params.atr_period <= 0 or params.atr_trail_multiplier < 0.0:
            raise ValueError("atr parameters are invalid")
        if params.regime_band < 0.0 or params.regime_band >= 1.0:
            raise ValueError("regime-band must be in [0,1)")

    @staticmethod
    def _exponential_moving_average(values: list[float], length: int) -> list[float]:
        n = len(values)
        ema = [math.nan] * n
        if length <= 0 or n < length:
            return ema

        seed = sum(values[:length])
        ema[length - 1] = seed / length

        alpha = 2.0 / (length + 1.0)
        for i in range(length, n):
            ema[i] = (alpha * values[i]) + ((1.0 - alpha) * ema[i - 1])
        return ema

    @staticmethod
    def _wilder_atr(high: list[float], low: list[float], close: list[float], period: int) -> list[float]:
        n = len(close)
        atr = [math.nan] * n
        if n < period:
            return atr

        tr = [0.0] * n
        tr[0] = high[0] - low[0]
        for i in range(1, n):
            high_low = high[i] - low[i]
            high_prev_close = abs(high[i] - close[i - 1])
            low_prev_close = abs(low[i] - close[i - 1])
            tr[i] = max(high_low, high_prev_close, low_prev_close)

        total = sum(tr[:period])
        first = period - 1
        atr[first] = total / period
        for i in range(period, n):
            atr[i] = ((atr[i - 1] * (period - 1)) + tr[i]) / period
        return atr

    @staticmethod
    def _max_drawdown(equity: list[float]) -> float:
        peak = equity[0]
        mdd = 0.0
        for value in equity:
            peak = max(peak, value)
            mdd = min(mdd, (value / peak) - 1.0)
        return mdd

    @staticmethod
    def _count_trade_executions(trade: list[float]) -> int:
        return sum(1 for turnover in trade if turnover > 1e-12)


class GridSearchServiceV4:
    def __init__(self, backtest_service: BacktestServiceV4) -> None:
        self.backtest_service = backtest_service

    def search(self, daily_bars: list[CandleBar], config: BacktestConfigV4) -> list[GridSearchRowV4]:
        ma_len_values = config.resolve_ma_len_values()
        atr_period_values = config.resolve_atr_period_values()
        atr_trail_values = config.resolve_atr_trail_multipliers()
        regime_band_values = config.resolve_regime_band_values()
        weekly_ema_len_values = config.resolve_weekly_ema_len_values()

        sizes = [
            len(ma_len_values),
            len(atr_period_values),
            len(atr_trail_values),
            len(regime_band_values),
            len(weekly_ema_len_values),
        ]
        total = config.combination_count()
        strides = _build_strides(sizes)

        def evaluator(index: int) -> GridSearchRowV4:
            c0 = _coord(index, strides[0], sizes[0])
            c1 = _coord(index, strides[1], sizes[1])
            c2 = _coord(index, strides[2], sizes[2])
            c3 = _coord(index, strides[3], sizes[3])
            c4 = _coord(index, strides[4], sizes[4])
            params = StrategyParamsV4(
                fee_per_side=config.fee_per_side,
                slippage=config.slippage,
                regime_ema_len=ma_len_values[c0],
                atr_period=atr_period_values[c1],
                atr_trail_multiplier=atr_trail_values[c2],
                regime_band=regime_band_values[c3],
                weekly_ema_len=weekly_ema_len_values[c4],
            )
            result = self.backtest_service.backtest_daily_strategy(daily_bars, params)
            cagr = result.summary.cagr
            mdd = result.summary.mdd
            calmar_like = math.nan if mdd == 0.0 else cagr / abs(mdd)
            return GridSearchRowV4(
                params=params,
                calmar_like=calmar_like,
                cagr=cagr,
                mdd=mdd,
                final_equity=result.summary.final_equity,
            )

        indices = list(range(total))
        if config.grid_parallelism <= 1:
            rows = [evaluator(index) for index in indices]
        else:
            with ThreadPoolExecutor(max_workers=config.grid_parallelism) as pool:
                rows = list(pool.map(evaluator, indices))

        rows.sort(
            key=lambda row: (
                _rank_value(row.calmar_like),
                _rank_value(row.cagr),
                _rank_value(row.final_equity),
            ),
            reverse=True,
        )
        return rows[: config.top_k]


def _coord(index: int, stride: int, size: int) -> int:
    return (index // stride) % size


def _build_strides(sizes: list[int]) -> list[int]:
    strides = [1] * len(sizes)
    stride = 1
    for i in range(len(sizes) - 1, -1, -1):
        strides[i] = stride
        stride *= sizes[i]
    return strides


def _parse_double_range(spec: str) -> list[float]:
    parts = [p.strip() for p in spec.split(":")]
    if len(parts) != 3:
        raise ValueError(f"range must be start:end:step, got: {spec}")
    start, end, step = float(parts[0]), float(parts[1]), float(parts[2])
    if step <= 0:
        raise ValueError(f"range step must be > 0, got: {spec}")
    if end < start:
        raise ValueError(f"range end must be >= start, got: {spec}")

    out: list[float] = []
    value = start
    while value <= end + 1e-12:
        out.append(value)
        value += step
    return out


def _parse_positive_int_range(spec: str, field_name: str) -> list[int]:
    out = [int(round(value)) for value in _parse_double_range(spec)]
    for value in out:
        if value <= 0:
            raise ValueError(f"{field_name} values must be > 0")
    return out


def _rank_value(value: float) -> float:
    return value if math.isfinite(value) else float("-inf")
