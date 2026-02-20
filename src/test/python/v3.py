"""Backtest V3 migrated from V2 architecture.

Strategy: regime transition (BEAR->BULL, BULL->BEAR) + ATR trailing stop
with volatility-targeted dynamic exposure.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import math
import os
import time
import urllib.parse
import urllib.request

UPBIT_DAYS_URL = "https://api.upbit.com/v1/candles/days"
CSV_HEADER = [
    "candle_date_time_utc",
    "opening_price",
    "high_price",
    "low_price",
    "trade_price",
    "candle_acc_trade_volume",
]
MIN_EQUITY = 1e-12


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
class StrategyParamsV3:
    fee_per_side: float
    slippage: float
    regime_ema_len: int
    atr_period: int
    atr_trail_multiplier: float
    regime_band: float
    vol_target: float
    max_leverage: float
    min_exposure: float | None = None


@dataclass(frozen=True)
class BacktestRowV3:
    timestamp: datetime
    open: float
    close: float
    ma: float
    rsi: float
    buy_signal: bool
    sell_signal: bool
    setup_buy: bool
    setup_sell: bool
    trail_stop_triggered: bool
    regime: str
    regime_anchor: float
    regime_upper: float
    regime_lower: float
    atr: float
    vol_proxy: float
    target_exposure: float
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
class BacktestResultV3:
    rows: list[BacktestRowV3]
    summary: BacktestSummary


@dataclass(frozen=True)
class GridSearchRowV3:
    params: StrategyParamsV3
    calmar_like: float
    cagr: float
    mdd: float
    final_equity: float


@dataclass(frozen=True)
class BacktestConfigV3:
    enabled: bool = True
    market: str = "KRW-BTC"
    from_dt: datetime = datetime(2020, 1, 1, tzinfo=timezone.utc)
    to_dt: datetime | None = None
    csv_cache_dir: str = "outputs/data/upbit-cache"
    validation_ratio: float = 0.7
    fee_per_side: float = 0.0005
    slippage: float = 0.0002
    top_k: int = 10
    grid_parallelism: int = max(1, os.cpu_count() or 1)
    grid_progress_log_seconds: int = 5
    grid_rsi_range: str = "10:60:10"
    grid_range_rsi_range: str = "10:40:10"
    grid_bear_rsi_range: str = "10:30:10"
    grid_ma_len_range: str = "200:200:1"
    grid_ma_slope_range: str = "1:19:3"
    grid_atr_period_range: str = "14:14:1"
    grid_atr_trail_mult_range: str = "3:3:1"
    grid_regime_band_range: str = "0.02:0.02:0.01"
    grid_vol_target_range: str = "0.02:0.02:0.01"
    grid_max_leverage_range: str = "2:2:0.5"
    grid_min_exposure_range: str = "0:0:1"

    def __post_init__(self) -> None:
        if not self.market.strip():
            raise ValueError("market must not be blank")
        if not self.csv_cache_dir.strip():
            raise ValueError("csv-cache-dir must not be blank")
        if not (0.0 < self.validation_ratio < 1.0):
            raise ValueError("validation-ratio must be between 0 and 1")
        if self.fee_per_side < 0 or self.slippage < 0:
            raise ValueError("fee-per-side and slippage must be >= 0")
        if self.top_k <= 0:
            raise ValueError("top-k must be > 0")
        if self.grid_parallelism <= 0:
            raise ValueError("grid-parallelism must be > 0")
        if self.grid_progress_log_seconds <= 0:
            raise ValueError("grid-progress-log-seconds must be > 0")

    @property
    def resolved_to_dt(self) -> datetime:
        return self.to_dt or datetime.now(timezone.utc)

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

    def resolve_vol_target_values(self) -> list[float]:
        values = _parse_double_range(self.grid_vol_target_range)
        for value in values:
            if value <= 0.0:
                raise ValueError("grid-vol-target-range values must be > 0")
        return values

    def resolve_max_leverage_values(self) -> list[float]:
        values = _parse_double_range(self.grid_max_leverage_range)
        for value in values:
            if value <= 0.0:
                raise ValueError("grid-max-leverage-range values must be > 0")
        return values

    def resolve_min_exposure_values(self) -> list[float | None]:
        values = _parse_double_range(self.grid_min_exposure_range)
        out: list[float | None] = []
        for value in values:
            if value < 0.0:
                raise ValueError("grid-min-exposure-range values must be >= 0")
            out.append(value)
        return out

    def combination_count(self) -> int:
        total = 1
        for size in (
            len(self.resolve_ma_len_values()),
            len(self.resolve_atr_period_values()),
            len(self.resolve_atr_trail_multipliers()),
            len(self.resolve_regime_band_values()),
            len(self.resolve_vol_target_values()),
            len(self.resolve_max_leverage_values()),
            len(self.resolve_min_exposure_values()),
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


class BacktestServiceV3:
    def backtest_daily_strategy(self, daily_bars: list[CandleBar], params: StrategyParamsV3) -> BacktestResultV3:
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
        vol_proxy = self._resolve_vol_proxy(atr, close)

        buy_signal = [False] * n
        sell_signal = [False] * n
        setup_buy = [False] * n
        setup_sell = [False] * n
        trail_stop_triggered = [False] * n
        atr_trail_stop = [math.nan] * n
        pos_close_exposure = [0.0] * n
        pos_open_exposure = [0.0] * n
        target_exposure = [0.0] * n
        ret_oo = [0.0] * n
        trade = [0.0] * n
        equity = [0.0] * n
        equity_bh = [0.0] * n

        for i in range(n - 1):
            ret_oo[i] = (open_[i + 1] / open_[i]) - 1.0

        highest_close_since_entry = math.nan
        for i in range(n):
            pos_open_exposure[i] = 0.0 if i == 0 else pos_close_exposure[i - 1]
            trade[i] = abs(pos_open_exposure[i]) if i == 0 else abs(pos_open_exposure[i] - pos_open_exposure[i - 1])

            previous_open_exposure = 0.0 if i == 0 else pos_open_exposure[i - 1]
            current_open_exposure = pos_open_exposure[i]
            if current_open_exposure > previous_open_exposure and current_open_exposure > 0.0:
                highest_close_since_entry = close[i]
            elif current_open_exposure > 0.0:
                highest_close_since_entry = max(highest_close_since_entry, close[i]) if math.isfinite(highest_close_since_entry) else close[i]
            else:
                highest_close_since_entry = math.nan

            base_buy = self._base_buy_signal(i, regimes)
            base_sell = self._base_sell_signal(i, regimes, current_open_exposure)
            trail_stop = self._evaluate_atr_trail_stop(
                i,
                close,
                atr,
                highest_close_since_entry,
                current_open_exposure,
                params,
            )

            bullish_active = regimes[i] == MarketRegime.BULL
            should_zero = (not bullish_active) or trail_stop[1] or base_sell
            desired_exposure = self._target_exposure_from_vol_proxy(vol_proxy[i], params)

            next_exposure = 0.0
            if not should_zero:
                if base_buy or current_open_exposure > 0.0:
                    next_exposure = desired_exposure

            setup_buy[i] = base_buy
            setup_sell[i] = base_sell
            trail_stop_triggered[i] = trail_stop[1]
            atr_trail_stop[i] = trail_stop[0]
            target_exposure[i] = next_exposure
            buy_signal[i] = next_exposure > current_open_exposure
            sell_signal[i] = next_exposure < current_open_exposure
            pos_close_exposure[i] = next_exposure

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

        rows = [
            BacktestRowV3(
                timestamp=daily_bars[i].timestamp,
                open=open_[i],
                close=close[i],
                ma=regime_ema[i],
                rsi=math.nan,
                buy_signal=buy_signal[i],
                sell_signal=sell_signal[i],
                setup_buy=setup_buy[i],
                setup_sell=setup_sell[i],
                trail_stop_triggered=trail_stop_triggered[i],
                regime=regimes[i].value,
                regime_anchor=anchor[i],
                regime_upper=upper[i],
                regime_lower=lower[i],
                atr=atr[i],
                vol_proxy=vol_proxy[i],
                target_exposure=target_exposure[i],
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
        return BacktestResultV3(rows=rows, summary=summary)

    @staticmethod
    def _base_buy_signal(index: int, regimes: list[MarketRegime]) -> bool:
        if index <= 0:
            return False
        return regimes[index - 1] == MarketRegime.BEAR and regimes[index] == MarketRegime.BULL

    @staticmethod
    def _base_sell_signal(index: int, regimes: list[MarketRegime], current_open_exposure: float) -> bool:
        if current_open_exposure <= 0.0 or index <= 0:
            return False
        return regimes[index - 1] == MarketRegime.BULL and regimes[index] == MarketRegime.BEAR

    @staticmethod
    def _evaluate_atr_trail_stop(
        index: int,
        close: list[float],
        atr: list[float],
        highest_close_since_entry: float,
        current_open_exposure: float,
        params: StrategyParamsV3,
    ) -> tuple[float, bool]:
        if params.atr_trail_multiplier <= 0.0 or current_open_exposure <= 0.0:
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

    @staticmethod
    def _resolve_vol_proxy(atr: list[float], close: list[float]) -> list[float]:
        out = [math.nan] * len(close)
        for i in range(len(close)):
            if math.isfinite(atr[i]) and close[i] > 0.0 and math.isfinite(close[i]):
                out[i] = atr[i] / close[i]
        return out

    @staticmethod
    def _target_exposure_from_vol_proxy(vol_proxy: float, params: StrategyParamsV3) -> float:
        floor = params.min_exposure if params.min_exposure is not None else 0.0
        if not math.isfinite(vol_proxy) or vol_proxy <= 0.0:
            return floor
        raw = params.vol_target / vol_proxy
        return max(floor, min(params.max_leverage, raw))

    @staticmethod
    def _validate_params(params: StrategyParamsV3) -> None:
        if params is None:
            raise ValueError("strategy params are required")
        if params.fee_per_side < 0 or params.slippage < 0:
            raise ValueError("fee/slippage must be >= 0")
        if params.regime_ema_len <= 0:
            raise ValueError("regime-ema-len must be > 0")
        if params.atr_period <= 0 or params.atr_trail_multiplier < 0.0:
            raise ValueError("atr parameters are invalid")
        if params.regime_band < 0.0 or params.regime_band >= 1.0:
            raise ValueError("regime-band must be in [0,1)")
        if params.vol_target <= 0.0:
            raise ValueError("vol-target must be > 0")
        if params.max_leverage <= 0.0:
            raise ValueError("max-leverage must be > 0")

        min_exposure = params.min_exposure if params.min_exposure is not None else 0.0
        if min_exposure < 0.0:
            raise ValueError("min-exposure must be >= 0")
        if min_exposure > params.max_leverage:
            raise ValueError("min-exposure must be <= max-leverage")

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


class GridSearchServiceV3:
    def __init__(self, backtest_service: BacktestServiceV3) -> None:
        self.backtest_service = backtest_service

    def search(self, daily_bars: list[CandleBar], config: BacktestConfigV3) -> list[GridSearchRowV3]:
        ma_len_values = config.resolve_ma_len_values()
        atr_period_values = config.resolve_atr_period_values()
        atr_trail_values = config.resolve_atr_trail_multipliers()
        regime_band_values = config.resolve_regime_band_values()
        vol_target_values = config.resolve_vol_target_values()
        max_leverage_values = config.resolve_max_leverage_values()
        min_exposure_values = config.resolve_min_exposure_values()

        sizes = [
            len(ma_len_values),
            len(atr_period_values),
            len(atr_trail_values),
            len(regime_band_values),
            len(vol_target_values),
            len(max_leverage_values),
            len(min_exposure_values),
        ]
        total = config.combination_count()
        strides = _build_strides(sizes)

        def evaluator(index: int) -> GridSearchRowV3:
            c0 = _coord(index, strides[0], sizes[0])
            c1 = _coord(index, strides[1], sizes[1])
            c2 = _coord(index, strides[2], sizes[2])
            c3 = _coord(index, strides[3], sizes[3])
            c4 = _coord(index, strides[4], sizes[4])
            c5 = _coord(index, strides[5], sizes[5])
            c6 = _coord(index, strides[6], sizes[6])
            params = StrategyParamsV3(
                fee_per_side=config.fee_per_side,
                slippage=config.slippage,
                regime_ema_len=ma_len_values[c0],
                atr_period=atr_period_values[c1],
                atr_trail_multiplier=atr_trail_values[c2],
                regime_band=regime_band_values[c3],
                vol_target=vol_target_values[c4],
                max_leverage=max_leverage_values[c5],
                min_exposure=min_exposure_values[c6],
            )
            result = self.backtest_service.backtest_daily_strategy(daily_bars, params)
            cagr = result.summary.cagr
            mdd = result.summary.mdd
            calmar_like = math.nan if mdd == 0.0 else cagr / abs(mdd)
            return GridSearchRowV3(
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


class UpbitDataServiceV3:
    def __init__(self) -> None:
        self._last_request_at_ms = 0

    def load_bars(self, config: BacktestConfigV3) -> list[CandleBar]:
        cache_path = self._resolve_cache_path(
            config.market,
            config.from_dt,
            config.resolved_to_dt,
            config.csv_cache_dir,
            "days",
        )
        if os.path.exists(cache_path):
            try:
                return self._load_csv(cache_path)
            except Exception:
                pass

        bars = self.fetch_from_api(UPBIT_DAYS_URL, config.market, config.from_dt, config.resolved_to_dt)
        self._save_csv(cache_path, bars)
        return bars

    def fetch_from_api(self, endpoint: str, market: str, from_dt: datetime, to_dt: datetime) -> list[CandleBar]:
        dedup: dict[str, CandleBar] = {}
        cursor = to_dt
        while True:
            batch = self._request_batch(endpoint, market, cursor, 200)
            if not batch:
                break

            oldest = batch[-1].timestamp
            for bar in batch:
                if bar.timestamp < from_dt or bar.timestamp > to_dt:
                    continue
                dedup[bar.timestamp.isoformat()] = bar

            if oldest <= from_dt:
                break
            cursor = oldest.fromtimestamp(oldest.timestamp() - 1, tz=timezone.utc)

        out = list(dedup.values())
        out.sort(key=lambda row: row.timestamp)
        return out

    def _request_batch(self, endpoint: str, market: str, to_dt: datetime, count: int) -> list[CandleBar]:
        params = urllib.parse.urlencode({"market": market, "to": to_dt.isoformat(), "count": count})
        url = f"{endpoint}?{params}"

        self._wait_rate_limit_window()
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "evergreen-backtest/1.0"})
        with urllib.request.urlopen(req, timeout=15) as response:
            payload = response.read().decode("utf-8")

        raw = json.loads(payload)
        bars = [
            CandleBar(
                timestamp=_parse_ts(row["candle_date_time_utc"]),
                open=float(row["opening_price"]),
                high=float(row["high_price"]),
                low=float(row["low_price"]),
                close=float(row["trade_price"]),
                volume=float(row["candle_acc_trade_volume"]),
            )
            for row in raw
        ]
        bars.sort(key=lambda row: row.timestamp, reverse=True)
        return bars

    def _wait_rate_limit_window(self) -> None:
        now_ms = int(time.time() * 1000)
        wait_ms = 150 - (now_ms - self._last_request_at_ms)
        if wait_ms > 0:
            time.sleep(wait_ms / 1000)
        self._last_request_at_ms = int(time.time() * 1000)

    def _resolve_cache_path(self, market: str, from_dt: datetime, to_dt: datetime, cache_dir: str, interval_key: str) -> str:
        safe_market = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in market)
        from_key = from_dt.astimezone(timezone.utc).strftime("%Y%m%d")
        to_key = to_dt.astimezone(timezone.utc).strftime("%Y%m%d")
        file_name = f"{safe_market}_{from_key}_{to_key}_{interval_key}.csv"
        return os.path.join(cache_dir, file_name)

    def _load_csv(self, path: str) -> list[CandleBar]:
        dedup: dict[str, CandleBar] = {}
        with open(path, "r", encoding="utf-8", newline="") as fp:
            reader = csv.reader(fp)
            _ = next(reader, None)
            for parts in reader:
                if len(parts) < 6:
                    continue
                ts = _parse_ts(parts[0])
                dedup[ts.isoformat()] = CandleBar(
                    timestamp=ts,
                    open=float(parts[1]),
                    high=float(parts[2]),
                    low=float(parts[3]),
                    close=float(parts[4]),
                    volume=float(parts[5]),
                )
        out = list(dedup.values())
        out.sort(key=lambda row: row.timestamp)
        return out

    def _save_csv(self, path: str, bars: list[CandleBar]) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(CSV_HEADER)
            for bar in bars:
                writer.writerow([bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume])


@dataclass(frozen=True)
class BacktestRunOutputV3:
    validation_result: BacktestResultV3
    test_result: BacktestResultV3
    full_result: BacktestResultV3
    selected_params: StrategyParamsV3
    candidates: list[GridSearchRowV3]


class BacktestRunnerV3:
    def __init__(
        self,
        config: BacktestConfigV3,
        upbit_data_service: UpbitDataServiceV3 | None = None,
        backtest_service: BacktestServiceV3 | None = None,
        grid_search_service: GridSearchServiceV3 | None = None,
    ) -> None:
        self.config = config
        self.upbit_data_service = upbit_data_service or UpbitDataServiceV3()
        self.backtest_service = backtest_service or BacktestServiceV3()
        self.grid_search_service = grid_search_service or GridSearchServiceV3(self.backtest_service)

    def run(self) -> BacktestRunOutputV3 | None:
        if not self.config.enabled:
            return None

        bars = self.upbit_data_service.load_bars(self.config)
        split_index = self.config.validation_split_index(len(bars))
        validation_bars = bars[:split_index]
        test_bars = bars[split_index:]

        rows = self.grid_search_service.search(validation_bars, self.config)
        if not rows:
            raise RuntimeError("Grid search returned no rows")

        selected_params = rows[0].params
        validation_result = self.backtest_service.backtest_daily_strategy(validation_bars, selected_params)
        test_result = self.backtest_service.backtest_daily_strategy(test_bars, selected_params)
        full_result = self.backtest_service.backtest_daily_strategy(bars, selected_params)
        return BacktestRunOutputV3(
            validation_result=validation_result,
            test_result=test_result,
            full_result=full_result,
            selected_params=selected_params,
            candidates=rows,
        )


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


def _parse_ts(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith("Z") or text.endswith("z"):
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00")).astimezone(timezone.utc)
    return datetime.fromisoformat(f"{text}+00:00").astimezone(timezone.utc)


def _rank_value(value: float) -> float:
    return value if math.isfinite(value) else float("-inf")
