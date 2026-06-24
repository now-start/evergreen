"""V1: 이동평균 추세 필터와 RSI 과매도 진입을 결합한 단순 기준 전략."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import math

from evergreen_backtest.contracts import action_from_signals, target_position_ratio_from_signals

MIN_EQUITY = 1e-12
RSI_PERIOD = 14


@dataclass(frozen=True)
class CandleBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class StrategyParamsV1:
    fee_per_side: float
    slippage: float
    rsi_buy: float
    ma_len: int
    ma_slope_days: int


@dataclass(frozen=True)
class BacktestRowV1:
    timestamp: datetime
    open: float
    close: float
    ma: float
    rsi: float
    action: str
    target_position_ratio: float
    buy_signal: bool
    sell_signal: bool
    pos_open: int
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
class BacktestResultV1:
    rows: list[BacktestRowV1]
    summary: BacktestSummary


@dataclass(frozen=True)
class GridSearchRowV1:
    params: StrategyParamsV1
    calmar_like: float
    cagr: float
    mdd: float
    final_equity: float


@dataclass(frozen=True)
class BacktestConfigV1:
    enabled: bool = True
    validation_ratio: float = 0.7
    fee_per_side: float = 0.0005
    slippage: float = 0.0002
    top_k: int = 1
    grid_rsi_range: str = "20:45:5"
    grid_ma_len_range: str = "20:120:10"
    grid_ma_slope_range: str = "1:9:2"

    def __post_init__(self) -> None:
        if not (0.0 < self.validation_ratio < 1.0):
            raise ValueError("validation-ratio must be between 0 and 1")
        if self.fee_per_side < 0 or self.slippage < 0:
            raise ValueError("fee-per-side and slippage must be >= 0")
        if self.top_k <= 0:
            raise ValueError("top-k must be > 0")

    def resolve_rsi_values(self) -> list[float]:
        return _parse_double_range(self.grid_rsi_range)

    def resolve_ma_len_values(self) -> list[int]:
        return _parse_int_range(self.grid_ma_len_range)

    def resolve_ma_slope_values(self) -> list[int]:
        return _parse_int_range(self.grid_ma_slope_range)

    def combination_count(self) -> int:
        return (
            len(self.resolve_rsi_values())
            * len(self.resolve_ma_len_values())
            * len(self.resolve_ma_slope_values())
        )

    def validation_split_index(self, total_bars: int) -> int:
        if total_bars < 4:
            raise ValueError("At least 4 bars are required for validation/test split")
        split = int(math.floor(total_bars * self.validation_ratio))
        split = max(2, split)
        split = min(total_bars - 2, split)
        return split


class BacktestServiceV1:
    def backtest_daily_strategy(self, bars: list[CandleBar], params: StrategyParamsV1) -> BacktestResultV1:
        if bars is None or len(bars) < 2:
            raise ValueError("At least 2 bars are required")
        self._validate_params(params)

        n = len(bars)
        open_ = [bar.open for bar in bars]
        close = [bar.close for bar in bars]

        ma = self._moving_average(close, params.ma_len)
        rsi = self._wilder_rsi(close, RSI_PERIOD)

        buy_signal = [False] * n
        sell_signal = [False] * n
        pos_close = [0] * n
        pos_open = [0] * n
        ret_oo = [0.0] * n
        trade = [0.0] * n
        equity = [0.0] * n
        equity_bh = [0.0] * n

        for i in range(n - 1):
            ret_oo[i] = (open_[i + 1] / open_[i]) - 1.0

        for i in range(n):
            slope_ok = (
                i - params.ma_slope_days >= 0
                and math.isfinite(ma[i])
                and math.isfinite(ma[i - params.ma_slope_days])
                and ma[i] > ma[i - params.ma_slope_days]
            )
            bull = math.isfinite(ma[i]) and close[i] > ma[i] and slope_ok
            buy_signal[i] = bull and math.isfinite(rsi[i]) and rsi[i] < params.rsi_buy
            sell_signal[i] = math.isfinite(ma[i]) and close[i] < ma[i]

            prev = 0 if i == 0 else pos_close[i - 1]
            if not math.isfinite(ma[i]) or not math.isfinite(rsi[i]):
                pos_close[i] = 0
            elif sell_signal[i]:
                pos_close[i] = 0
            elif buy_signal[i]:
                pos_close[i] = 1
            else:
                pos_close[i] = prev

            pos_open[i] = 0 if i == 0 else pos_close[i - 1]
            trade[i] = abs(pos_open[i]) if i == 0 else abs(pos_open[i] - pos_open[i - 1])

        cost_unit = params.fee_per_side + params.slippage
        equity[0] = 1.0
        equity_bh[0] = 1.0 - cost_unit

        for i in range(1, n):
            cost = trade[i] * cost_unit
            growth = 1.0 + (ret_oo[i] if pos_open[i] == 1 else 0.0) - cost
            equity[i] = max(MIN_EQUITY, equity[i - 1] * growth)
            equity_bh[i] = max(MIN_EQUITY, equity_bh[i - 1] * (1.0 + ret_oo[i]))

        final_equity = equity[-1]
        final_equity_bh = equity_bh[-1]
        years = max(1.0 / 365.25, ((bars[-1].timestamp - bars[0].timestamp).days / 365.25))
        cagr = math.pow(final_equity, 1.0 / years) - 1.0
        mdd = self._max_drawdown(equity)
        trades = int(round(sum(trade)))

        rows = [
            BacktestRowV1(
                timestamp=bars[i].timestamp,
                open=open_[i],
                close=close[i],
                ma=ma[i],
                rsi=rsi[i],
                action=action_from_signals(buy_signal[i], sell_signal[i]).value,
                target_position_ratio=target_position_ratio_from_signals(buy_signal[i], sell_signal[i], float(pos_open[i])),
                buy_signal=buy_signal[i],
                sell_signal=sell_signal[i],
                pos_open=pos_open[i],
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
            range=f"{bars[0].timestamp} -> {bars[-1].timestamp}",
        )
        return BacktestResultV1(rows=rows, summary=summary)

    def _validate_params(self, params: StrategyParamsV1) -> None:
        if params is None:
            raise ValueError("Strategy params are required")
        if params.ma_len <= 0:
            raise ValueError("ma-len must be > 0")
        if params.ma_slope_days < 0:
            raise ValueError("ma-slope-days must be >= 0")
        if params.fee_per_side < 0 or params.slippage < 0:
            raise ValueError("fee/slippage must be >= 0")

    @staticmethod
    def _moving_average(values: list[float], length: int) -> list[float]:
        out = [math.nan] * len(values)
        window_sum = 0.0
        for i, value in enumerate(values):
            window_sum += value
            if i >= length:
                window_sum -= values[i - length]
            if i >= length - 1:
                out[i] = window_sum / length
        return out

    @staticmethod
    def _wilder_rsi(close: list[float], period: int) -> list[float]:
        n = len(close)
        rsi = [math.nan] * n
        if n <= period:
            return rsi

        gain_sum = 0.0
        loss_sum = 0.0
        for i in range(1, period + 1):
            diff = close[i] - close[i - 1]
            if diff > 0:
                gain_sum += diff
            else:
                loss_sum += -diff

        avg_gain = gain_sum / period
        avg_loss = loss_sum / period
        rsi[period] = math.nan if avg_loss == 0.0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))

        for i in range(period + 1, n):
            diff = close[i] - close[i - 1]
            gain = max(0.0, diff)
            loss = max(0.0, -diff)
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period
            rsi[i] = math.nan if avg_loss == 0.0 else 100.0 - (100.0 / (1.0 + (avg_gain / avg_loss)))
        return rsi

    @staticmethod
    def _max_drawdown(equity: list[float]) -> float:
        peak = equity[0]
        mdd = 0.0
        for value in equity:
            peak = max(peak, value)
            mdd = min(mdd, (value / peak) - 1.0)
        return mdd


class GridSearchServiceV1:
    def __init__(self, backtest_service: BacktestServiceV1) -> None:
        self.backtest_service = backtest_service

    def search(self, bars: list[CandleBar], config: BacktestConfigV1) -> list[GridSearchRowV1]:
        rows: list[GridSearchRowV1] = []
        for rsi_buy in config.resolve_rsi_values():
            for ma_len in config.resolve_ma_len_values():
                for ma_slope in config.resolve_ma_slope_values():
                    params = StrategyParamsV1(
                        fee_per_side=config.fee_per_side,
                        slippage=config.slippage,
                        rsi_buy=rsi_buy,
                        ma_len=ma_len,
                        ma_slope_days=ma_slope,
                    )
                    result = self.backtest_service.backtest_daily_strategy(bars, params)
                    cagr = result.summary.cagr
                    mdd = result.summary.mdd
                    calmar = math.nan if mdd == 0.0 else cagr / abs(mdd)
                    rows.append(
                        GridSearchRowV1(
                            params=params,
                            calmar_like=calmar,
                            cagr=cagr,
                            mdd=mdd,
                            final_equity=result.summary.final_equity,
                        )
                    )

        rows.sort(
            key=lambda r: (
                _rank_value(r.calmar_like),
                _rank_value(r.cagr),
                _rank_value(r.final_equity),
            ),
            reverse=True,
        )
        return rows[: config.top_k]


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


def _parse_int_range(spec: str) -> list[int]:
    return [int(round(value)) for value in _parse_double_range(spec)]


def _rank_value(value: float) -> float:
    return value if math.isfinite(value) else float("-inf")
