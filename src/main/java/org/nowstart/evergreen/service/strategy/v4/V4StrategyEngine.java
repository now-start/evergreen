package org.nowstart.evergreen.service.strategy.v4;

import java.math.BigDecimal;
import java.time.LocalDate;
import java.time.ZoneOffset;
import java.time.temporal.WeekFields;
import java.util.ArrayList;
import java.util.List;
import org.nowstart.evergreen.data.type.MarketRegime;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.SignalAction;
import org.nowstart.evergreen.service.strategy.core.StrategyDiagnostic;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyInput;
import org.nowstart.evergreen.service.strategy.core.StrategyMath;
import org.nowstart.evergreen.service.strategy.core.StrategySignalDecision;
import org.nowstart.evergreen.service.strategy.core.TradingStrategyEngine;
import org.springframework.stereotype.Component;

@Component
public class V4StrategyEngine implements TradingStrategyEngine<V4StrategyOverrides> {

    public static final String VERSION = "v4";

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Class<V4StrategyOverrides> parameterType() {
        return V4StrategyOverrides.class;
    }

    @Override
    public int requiredWarmupCandles(V4StrategyOverrides params) {
        return Math.max(params.regimeEmaLen(), params.atrPeriod());
    }

    @Override
    public StrategyEvaluation evaluate(StrategyInput<V4StrategyOverrides> input) {
        StrategyMath.validateInput(input);

        List<OhlcvCandle> candles = input.candles();
        int signalIndex = input.signalIndex();
        V4StrategyOverrides params = input.params();
        PositionSnapshot position = input.position() == null ? PositionSnapshot.EMPTY : input.position();
        boolean hasPosition = position.hasPosition();

        double[] close = StrategyMath.close(candles);
        double[] high = StrategyMath.high(candles);
        double[] low = StrategyMath.low(candles);
        double[] regimeAnchor = StrategyMath.exponentialMovingAverage(close, params.regimeEmaLen());
        double[] atr = StrategyMath.wilderAtr(high, low, close, params.atrPeriod());
        double regimeBand = params.regimeBand().doubleValue();
        MarketRegime[] regimes = StrategyMath.resolveRegimes(close, regimeAnchor, regimeBand);
        WeeklyState weekly = weeklyState(candles, signalIndex, params.weeklyEmaLen());

        MarketRegime previousRegime = regimes[signalIndex - 1];
        MarketRegime currentRegime = regimes[signalIndex];
        boolean buySetup = previousRegime == MarketRegime.BEAR
                && currentRegime == MarketRegime.BULL
                && weekly.bullish();
        boolean sellSetup = hasPosition
                && previousRegime == MarketRegime.BULL
                && currentRegime == MarketRegime.BEAR;
        StrategyMath.TrailStopEvaluation trailStop = StrategyMath.evaluateTrailStop(
                candles,
                signalIndex,
                atr,
                params.atrTrailMultiplier().doubleValue(),
                position
        );

        boolean shouldBuy = !hasPosition && buySetup;
        boolean shouldSell = hasPosition && (sellSetup || trailStop.triggered());
        SignalAction action = StrategyMath.resolveAction(shouldBuy, shouldSell);
        BigDecimal targetPositionRatio = BigDecimal.valueOf(StrategyMath.targetRatio(
                action,
                StrategyMath.currentPositionRatio(position)
        ));

        double anchorValue = regimeAnchor[signalIndex];
        List<StrategyDiagnostic> diagnostics = List.of(
                StrategyDiagnostic.number("regime.anchor", "Regime Anchor", anchorValue),
                StrategyDiagnostic.number("regime.upper", "Regime Upper Band", upper(anchorValue, regimeBand)),
                StrategyDiagnostic.number("regime.lower", "Regime Lower Band", lower(anchorValue, regimeBand)),
                StrategyDiagnostic.number("weekly.close", "Weekly Close", weekly.close()),
                StrategyDiagnostic.number("weekly.ema", "Weekly EMA", weekly.ema()),
                StrategyDiagnostic.number("weekly.bullish", "Weekly Bullish", weekly.bullish() ? 1.0 : 0.0),
                StrategyDiagnostic.number("atr.trail_stop", "ATR Trail Stop", trailStop.stopPrice())
        );
        return new StrategyEvaluation(
                new StrategySignalDecision(
                        action,
                        resolveSignalReason(action, buySetup, sellSetup, trailStop.triggered()),
                        targetPositionRatio
                ),
                diagnostics
        );
    }

    private WeeklyState weeklyState(List<OhlcvCandle> candles, int signalIndex, int weeklyEmaLen) {
        WeekFields iso = WeekFields.ISO;
        List<String> weekKeys = new ArrayList<>();
        List<Double> weeklyCloses = new ArrayList<>();

        for (int i = 0; i <= signalIndex; i++) {
            LocalDate date = candles.get(i).timestamp().atOffset(ZoneOffset.UTC).toLocalDate();
            String key = date.get(iso.weekBasedYear()) + "-" + date.get(iso.weekOfWeekBasedYear());
            if (weekKeys.isEmpty() || !weekKeys.get(weekKeys.size() - 1).equals(key)) {
                weekKeys.add(key);
                weeklyCloses.add(candles.get(i).close());
            } else {
                weeklyCloses.set(weeklyCloses.size() - 1, candles.get(i).close());
            }
        }

        double[] values = new double[weeklyCloses.size()];
        for (int i = 0; i < weeklyCloses.size(); i++) {
            values[i] = weeklyCloses.get(i);
        }
        double[] ema = StrategyMath.exponentialMovingAverage(values, weeklyEmaLen);
        double weeklyClose = values[values.length - 1];
        double weeklyEma = ema[ema.length - 1];
        return new WeeklyState(
                weeklyClose,
                weeklyEma,
                Double.isFinite(weeklyEma) && weeklyClose >= weeklyEma
        );
    }

    private double upper(double anchorValue, double regimeBand) {
        return Double.isFinite(anchorValue) ? anchorValue * (1.0 + regimeBand) : Double.NaN;
    }

    private double lower(double anchorValue, double regimeBand) {
        return Double.isFinite(anchorValue) ? anchorValue * (1.0 - regimeBand) : Double.NaN;
    }

    private String resolveSignalReason(
            SignalAction action,
            boolean buySetup,
            boolean sellSetup,
            boolean trailStopTriggered
    ) {
        if (action == SignalAction.BUY) {
            return "BUY_REGIME_WEEKLY_FILTER";
        }
        if (action == SignalAction.SELL && sellSetup && trailStopTriggered) {
            return "SELL_REGIME_AND_TRAIL_STOP";
        }
        if (action == SignalAction.SELL && trailStopTriggered) {
            return "SELL_TRAIL_STOP";
        }
        if (action == SignalAction.SELL) {
            return "SELL_REGIME_TRANSITION";
        }
        if (buySetup) {
            return "SETUP_BUY";
        }
        if (sellSetup) {
            return "SETUP_SELL";
        }
        return "NONE";
    }

    private record WeeklyState(
            double close,
            double ema,
            boolean bullish
    ) {
    }
}
