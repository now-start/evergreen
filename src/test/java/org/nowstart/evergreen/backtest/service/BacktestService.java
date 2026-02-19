package org.nowstart.evergreen.backtest.service;

import org.nowstart.evergreen.backtest.model.BacktestResult;
import org.nowstart.evergreen.backtest.model.BacktestRow;
import org.nowstart.evergreen.backtest.model.BacktestSummary;
import org.nowstart.evergreen.backtest.model.CandleBar;
import org.nowstart.evergreen.backtest.model.StrategyParams;
import org.springframework.stereotype.Service;

import java.time.Duration;
import java.util.ArrayList;
import java.util.List;

@Service
public class BacktestService {

    private static final int RSI_PERIOD = 14;
    private static final double MIN_EQUITY = 1e-12;

    public BacktestResult backtestDailyStrategy(List<CandleBar> bars, StrategyParams params) {
        if (bars == null || bars.size() < 2) {
            throw new IllegalArgumentException("At least 2 bars are required");
        }
        validateParams(params);

        int n = bars.size();
        double[] open = new double[n];
        double[] close = new double[n];
        for (int i = 0; i < n; i++) {
            open[i] = bars.get(i).open();
            close[i] = bars.get(i).close();
        }

        double[] ma = movingAverage(close, params.maLen());
        double[] rsi = wilderRsi(close, RSI_PERIOD);

        boolean[] buySignal = new boolean[n];
        boolean[] sellSignal = new boolean[n];
        int[] posClose = new int[n];
        int[] posOpen = new int[n];
        double[] retOo = new double[n];
        double[] trade = new double[n];
        double[] equity = new double[n];
        double[] equityBh = new double[n];

        for (int i = 0; i < n - 1; i++) {
            retOo[i] = open[i + 1] / open[i] - 1.0;
        }
        retOo[n - 1] = 0.0;

        for (int i = 0; i < n; i++) {
            boolean slopeOk = i - params.maSlopeDays() >= 0 && !Double.isNaN(ma[i]) && !Double.isNaN(ma[i - params.maSlopeDays()])
                    && ma[i] > ma[i - params.maSlopeDays()];
            boolean bull = !Double.isNaN(ma[i]) && close[i] > ma[i] && slopeOk;
            buySignal[i] = bull && !Double.isNaN(rsi[i]) && rsi[i] < params.rsiBuy();
            sellSignal[i] = !Double.isNaN(ma[i]) && close[i] < ma[i];

            int prev = i == 0 ? 0 : posClose[i - 1];
            if (Double.isNaN(ma[i]) || Double.isNaN(rsi[i])) {
                posClose[i] = 0;
            } else if (sellSignal[i]) {
                posClose[i] = 0;
            } else if (buySignal[i]) {
                posClose[i] = 1;
            } else {
                posClose[i] = prev;
            }

            posOpen[i] = i == 0 ? 0 : posClose[i - 1];
            trade[i] = i == 0 ? Math.abs(posOpen[i]) : Math.abs(posOpen[i] - posOpen[i - 1]);
        }

        double costUnit = params.feePerSide() + params.slippage();
        equity[0] = 1.0;
        equityBh[0] = 1.0 - costUnit;

        for (int i = 1; i < n; i++) {
            double cost = trade[i] * costUnit;
            double growth = 1.0 + (posOpen[i] == 1 ? retOo[i] : 0.0) - cost;
            equity[i] = Math.max(MIN_EQUITY, equity[i - 1] * growth);
            equityBh[i] = Math.max(MIN_EQUITY, equityBh[i - 1] * (1.0 + retOo[i]));
        }

        double finalEquity = equity[n - 1];
        double finalEquityBh = equityBh[n - 1];
        double years = Math.max(1.0 / 365.25, Duration.between(bars.get(0).timestamp(), bars.get(n - 1).timestamp()).toDays() / 365.25);
        double cagr = Math.pow(finalEquity, 1.0 / years) - 1.0;
        double mdd = maxDrawdown(equity);
        int trades = (int) Math.round(sum(trade));

        List<BacktestRow> rows = new ArrayList<>(n);
        for (int i = 0; i < n; i++) {
            rows.add(new BacktestRow(
                    bars.get(i).timestamp(),
                    open[i],
                    close[i],
                    ma[i],
                    rsi[i],
                    buySignal[i],
                    sellSignal[i],
                    posOpen[i],
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
                bars.get(0).timestamp() + " -> " + bars.get(n - 1).timestamp()
        );

        return new BacktestResult(rows, summary);
    }

    private void validateParams(StrategyParams params) {
        if (params == null) {
            throw new IllegalArgumentException("Strategy params are required");
        }
        if (params.maLen() <= 0) {
            throw new IllegalArgumentException("ma-len must be > 0");
        }
        if (params.maSlopeDays() < 0) {
            throw new IllegalArgumentException("ma-slope-days must be >= 0");
        }
        if (params.feePerSide() < 0 || params.slippage() < 0) {
            throw new IllegalArgumentException("fee/slippage must be >= 0");
        }
    }

    private double[] movingAverage(double[] values, int len) {
        int n = values.length;
        double[] ma = new double[n];
        double sum = 0.0;
        for (int i = 0; i < n; i++) {
            sum += values[i];
            if (i >= len) {
                sum -= values[i - len];
            }
            ma[i] = i >= len - 1 ? sum / len : Double.NaN;
        }
        return ma;
    }

    private double[] wilderRsi(double[] close, int period) {
        int n = close.length;
        double[] rsi = new double[n];
        for (int i = 0; i < n; i++) {
            rsi[i] = Double.NaN;
        }
        if (n <= period) {
            return rsi;
        }

        double gainSum = 0.0;
        double lossSum = 0.0;
        for (int i = 1; i <= period; i++) {
            double diff = close[i] - close[i - 1];
            if (diff > 0) {
                gainSum += diff;
            } else {
                lossSum += -diff;
            }
        }

        double avgGain = gainSum / period;
        double avgLoss = lossSum / period;
        rsi[period] = avgLoss == 0 ? Double.NaN : 100.0 - (100.0 / (1.0 + (avgGain / avgLoss)));

        for (int i = period + 1; i < n; i++) {
            double diff = close[i] - close[i - 1];
            double gain = Math.max(0.0, diff);
            double loss = Math.max(0.0, -diff);
            avgGain = ((avgGain * (period - 1)) + gain) / period;
            avgLoss = ((avgLoss * (period - 1)) + loss) / period;
            rsi[i] = avgLoss == 0 ? Double.NaN : 100.0 - (100.0 / (1.0 + (avgGain / avgLoss)));
        }
        return rsi;
    }

    private double maxDrawdown(double[] equity) {
        double peak = equity[0];
        double mdd = 0.0;
        for (double v : equity) {
            peak = Math.max(peak, v);
            mdd = Math.min(mdd, v / peak - 1.0);
        }
        return mdd;
    }

    private double sum(double[] values) {
        double total = 0.0;
        for (double v : values) {
            total += v;
        }
        return total;
    }
}
