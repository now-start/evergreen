package org.nowstart.evergreen.backtest.service;

import org.nowstart.evergreen.backtest.model.BacktestResult;
import org.nowstart.evergreen.backtest.model.BacktestRow;
import org.nowstart.evergreen.backtest.model.BacktestSummary;
import org.nowstart.evergreen.backtest.model.CandleBar;
import org.nowstart.evergreen.backtest.model.StrategyParams;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;

@Service
public class BacktestService {

    private static final int FULL_POSITION_UNITS = 3;
    private static final double MIN_EQUITY = 1e-12;

    public BacktestResult backtestDailyStrategy(List<CandleBar> dailyBars, StrategyParams params) {
        if (dailyBars == null || dailyBars.size() < 2) {
            throw new IllegalArgumentException("At least 2 daily bars are required");
        }
        validateParams(params);

        int n = dailyBars.size();
        double[] open = new double[n];
        double[] high = new double[n];
        double[] low = new double[n];
        double[] close = new double[n];
        for (int i = 0; i < n; i++) {
            CandleBar bar = dailyBars.get(i);
            open[i] = bar.open();
            high[i] = bar.high();
            low[i] = bar.low();
            close[i] = bar.close();
        }

        double[] regimeEma = exponentialMovingAverage(close, params.regimeEmaLen());
        double[] atr = wilderAtr(high, low, close, params.atrPeriod());
        RegimeContext regimeContext = resolveRegimes(close, regimeEma, params.regimeBand());
        MarketRegime[] regimes = regimeContext.regimes();

        boolean[] buySignal = new boolean[n];
        boolean[] sellSignal = new boolean[n];
        boolean[] setupBuy = new boolean[n];
        boolean[] setupSell = new boolean[n];
        boolean[] trailStopTriggered = new boolean[n];
        double[] atrTrailStop = new double[n];
        Arrays.fill(atrTrailStop, Double.NaN);
        int[] posCloseUnits = new int[n];
        int[] posOpenUnits = new int[n];
        double[] posOpenExposure = new double[n];
        double[] retOo = new double[n];
        double[] trade = new double[n];
        double[] equity = new double[n];
        double[] equityBh = new double[n];

        for (int i = 0; i < n - 1; i++) {
            retOo[i] = open[i + 1] / open[i] - 1.0;
        }
        retOo[n - 1] = 0.0;

        double highestCloseSinceEntry = Double.NaN;
        for (int i = 0; i < n; i++) {
            posOpenUnits[i] = i == 0 ? 0 : posCloseUnits[i - 1];
            posOpenExposure[i] = posOpenUnits[i] / (double) FULL_POSITION_UNITS;
            trade[i] = i == 0 ? Math.abs(posOpenExposure[i]) : Math.abs(posOpenExposure[i] - posOpenExposure[i - 1]);

            int previousOpenUnits = i == 0 ? 0 : posOpenUnits[i - 1];
            int currentOpenUnits = posOpenUnits[i];
            if (currentOpenUnits > previousOpenUnits) {
                highestCloseSinceEntry = close[i];
            } else if (currentOpenUnits > 0) {
                highestCloseSinceEntry = Double.isFinite(highestCloseSinceEntry)
                        ? Math.max(highestCloseSinceEntry, close[i])
                        : close[i];
            } else {
                highestCloseSinceEntry = Double.NaN;
            }

            boolean baseBuy = baseBuySignal(i, regimes);
            boolean baseSell = baseSellSignal(i, regimes, currentOpenUnits);
            TrailStopState trailStopState = evaluateAtrTrailStop(
                    i,
                    close,
                    atr,
                    highestCloseSinceEntry,
                    currentOpenUnits,
                    params
            );
            boolean trailStop = trailStopState.triggered();

            int targetUnits = currentOpenUnits;
            if (trailStop || baseSell) {
                targetUnits = 0;
            } else if (baseBuy) {
                targetUnits = FULL_POSITION_UNITS;
            }

            setupBuy[i] = baseBuy;
            setupSell[i] = baseSell;
            trailStopTriggered[i] = trailStop;
            atrTrailStop[i] = trailStopState.stop();
            buySignal[i] = targetUnits > currentOpenUnits;
            sellSignal[i] = targetUnits < currentOpenUnits;
            posCloseUnits[i] = targetUnits;
        }

        double costUnit = params.feePerSide() + params.slippage();
        equity[0] = 1.0;
        equityBh[0] = Math.max(MIN_EQUITY, 1.0 - costUnit);
        for (int i = 1; i < n; i++) {
            double turnoverCost = trade[i] * costUnit;
            double gross = 1.0 + (posOpenExposure[i] * retOo[i]) - turnoverCost;
            if (!Double.isFinite(gross) || gross <= 0.0) {
                equity[i] = MIN_EQUITY;
            } else {
                equity[i] = Math.max(MIN_EQUITY, equity[i - 1] * gross);
            }

            double bhGross = 1.0 + retOo[i];
            if (!Double.isFinite(bhGross) || bhGross <= 0.0) {
                equityBh[i] = MIN_EQUITY;
            } else {
                equityBh[i] = Math.max(MIN_EQUITY, equityBh[i - 1] * bhGross);
            }
        }

        double finalEquity = equity[n - 1];
        double finalEquityBh = equityBh[n - 1];
        double years = Math.max(
                1.0 / 365.25,
                Duration.between(dailyBars.get(0).timestamp(), dailyBars.get(n - 1).timestamp()).toDays() / 365.25
        );
        double cagr = Math.pow(finalEquity, 1.0 / years) - 1.0;
        double mdd = maxDrawdown(equity);
        int trades = countTradeExecutions(trade);

        List<BacktestRow> rows = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            rows.add(new BacktestRow(
                    dailyBars.get(i).timestamp(),
                    open[i],
                    close[i],
                    regimeEma[i],
                    Double.NaN,
                    buySignal[i],
                    sellSignal[i],
                    setupBuy[i],
                    setupSell[i],
                    trailStopTriggered[i],
                    regimes[i].name(),
                    regimeContext.anchor()[i],
                    regimeContext.upper()[i],
                    regimeContext.lower()[i],
                    atrTrailStop[i],
                    posOpenExposure[i],
                    retOo[i],
                    equity[i],
                    equityBh[i],
                    trade[i]
            ));
        }

        BacktestSummary summary = new BacktestSummary(
                finalEquity,
                finalEquityBh,
                cagr,
                mdd,
                trades,
                dailyBars.get(0).timestamp() + " -> " + dailyBars.get(n - 1).timestamp()
        );
        return new BacktestResult(rows, summary);
    }

    private boolean baseBuySignal(int index, MarketRegime[] regimes) {
        if (index <= 0) {
            return false;
        }
        return regimes[index - 1] == MarketRegime.BEAR && regimes[index] == MarketRegime.BULL;
    }

    private boolean baseSellSignal(int index, MarketRegime[] regimes, int currentOpenUnits) {
        if (currentOpenUnits <= 0 || index <= 0) {
            return false;
        }
        return regimes[index - 1] == MarketRegime.BULL && regimes[index] == MarketRegime.BEAR;
    }

    private TrailStopState evaluateAtrTrailStop(
            int index,
            double[] close,
            double[] atr,
            double highestCloseSinceEntry,
            int currentOpenUnits,
            StrategyParams params
    ) {
        if (params.atrTrailMultiplier() <= 0.0 || currentOpenUnits <= 0) {
            return new TrailStopState(Double.NaN, false);
        }
        if (!Double.isFinite(atr[index]) || !Double.isFinite(highestCloseSinceEntry)) {
            return new TrailStopState(Double.NaN, false);
        }
        double stop = highestCloseSinceEntry - (params.atrTrailMultiplier() * atr[index]);
        return new TrailStopState(stop, close[index] <= stop);
    }

    private RegimeContext resolveRegimes(double[] close, double[] anchor, double regimeBand) {
        int n = close.length;
        MarketRegime[] regimes = new MarketRegime[n];
        double[] upper = new double[n];
        double[] lower = new double[n];
        Arrays.fill(upper, Double.NaN);
        Arrays.fill(lower, Double.NaN);

        for (int i = 0; i < n; i++) {
            if (!Double.isFinite(anchor[i])) {
                regimes[i] = MarketRegime.UNKNOWN;
                continue;
            }
            upper[i] = anchor[i] * (1.0 + regimeBand);
            lower[i] = anchor[i] * (1.0 - regimeBand);

            MarketRegime previous = i == 0 ? MarketRegime.UNKNOWN : regimes[i - 1];
            if (close[i] > upper[i]) {
                regimes[i] = MarketRegime.BULL;
            } else if (close[i] < lower[i]) {
                regimes[i] = MarketRegime.BEAR;
            } else if (previous != MarketRegime.UNKNOWN) {
                regimes[i] = previous;
            } else if (close[i] > anchor[i]) {
                regimes[i] = MarketRegime.BULL;
            } else if (close[i] < anchor[i]) {
                regimes[i] = MarketRegime.BEAR;
            } else {
                regimes[i] = MarketRegime.UNKNOWN;
            }
        }
        return new RegimeContext(regimes, anchor, upper, lower);
    }

    private void validateParams(StrategyParams params) {
        if (params == null) {
            throw new IllegalArgumentException("strategy params are required");
        }
        if (params.feePerSide() < 0 || params.slippage() < 0) {
            throw new IllegalArgumentException("fee/slippage must be >= 0");
        }
        if (params.regimeEmaLen() <= 0) {
            throw new IllegalArgumentException("regime-ema-len must be > 0");
        }
        if (params.atrPeriod() <= 0 || params.atrTrailMultiplier() < 0.0) {
            throw new IllegalArgumentException("atr parameters are invalid");
        }
        if (params.regimeBand() < 0.0 || params.regimeBand() >= 1.0) {
            throw new IllegalArgumentException("regime-band must be in [0,1)");
        }
    }

    private double[] exponentialMovingAverage(double[] values, int len) {
        int n = values.length;
        double[] ema = new double[n];
        Arrays.fill(ema, Double.NaN);
        if (len <= 0 || n < len) {
            return ema;
        }

        double seed = 0.0;
        for (int i = 0; i < len; i++) {
            seed += values[i];
        }
        ema[len - 1] = seed / len;

        double alpha = 2.0 / (len + 1.0);
        for (int i = len; i < n; i++) {
            ema[i] = (alpha * values[i]) + ((1.0 - alpha) * ema[i - 1]);
        }
        return ema;
    }

    private double[] wilderAtr(double[] high, double[] low, double[] close, int period) {
        int n = close.length;
        double[] atr = new double[n];
        Arrays.fill(atr, Double.NaN);
        if (n < period) {
            return atr;
        }

        double[] tr = new double[n];
        tr[0] = high[0] - low[0];
        for (int i = 1; i < n; i++) {
            double highLow = high[i] - low[i];
            double highPrevClose = Math.abs(high[i] - close[i - 1]);
            double lowPrevClose = Math.abs(low[i] - close[i - 1]);
            tr[i] = Math.max(highLow, Math.max(highPrevClose, lowPrevClose));
        }

        double sum = 0.0;
        for (int i = 0; i < period; i++) {
            sum += tr[i];
        }
        int first = period - 1;
        atr[first] = sum / period;
        for (int i = period; i < n; i++) {
            atr[i] = ((atr[i - 1] * (period - 1)) + tr[i]) / period;
        }
        return atr;
    }

    private double maxDrawdown(double[] equity) {
        double peak = equity[0];
        double mdd = 0.0;
        for (double value : equity) {
            peak = Math.max(peak, value);
            mdd = Math.min(mdd, value / peak - 1.0);
        }
        return mdd;
    }

    private int countTradeExecutions(double[] trade) {
        int count = 0;
        for (double turnover : trade) {
            if (turnover > 1e-12) {
                count++;
            }
        }
        return count;
    }

    private enum MarketRegime {
        BULL,
        BEAR,
        UNKNOWN
    }

    private record RegimeContext(
            MarketRegime[] regimes,
            double[] anchor,
            double[] upper,
            double[] lower
    ) {
    }

    private record TrailStopState(double stop, boolean triggered) {
    }
}
