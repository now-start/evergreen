package org.nowstart.evergreen.service.strategy.v6;

import java.math.BigDecimal;
import java.util.Arrays;
import java.util.List;
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
public class V6StrategyEngine implements TradingStrategyEngine<V6StrategyOverrides> {

    public static final String VERSION = "v6";
    private static final String CANDLE_INTERVAL_KEY = "minute_240";
    private static final int REQUIRED_WARMUP_CANDLES = 35;
    private static final double FULL_POSITION_RATIO = 1.0;
    private static final double POSITION_EPSILON = 1e-12;

    private final V6DeepLearningProfile deepLearningProfile;

    public V6StrategyEngine(V6DeepLearningProfile deepLearningProfile) {
        this.deepLearningProfile = deepLearningProfile == null ? V6DeepLearningProfile.disabled() : deepLearningProfile;
    }

    @Override
    public String version() {
        return VERSION;
    }

    @Override
    public Class<V6StrategyOverrides> parameterType() {
        return V6StrategyOverrides.class;
    }

    @Override
    public String candleIntervalKey(V6StrategyOverrides params) {
        return CANDLE_INTERVAL_KEY;
    }

    @Override
    public int requiredWarmupCandles(V6StrategyOverrides params) {
        return REQUIRED_WARMUP_CANDLES;
    }

    @Override
    public StrategyEvaluation evaluate(StrategyInput<V6StrategyOverrides> input) {
        StrategyMath.validateInput(input);

        List<OhlcvCandle> candles = input.candles();
        int signalIndex = input.signalIndex();
        PositionSnapshot position = input.position() == null ? PositionSnapshot.EMPTY : input.position();
        V6StrategyOverrides params = input.params();

        double[] close = StrategyMath.close(candles);
        Trend2Components trend = trend2Components(close);
        double trend2 = trend.trend2()[signalIndex];
        double ruleScale = params.ruleScale().doubleValue();
        double ruleScore = Double.isFinite(trend2) && ruleScale > 0.0
                ? Math.abs(trend2) / ruleScale
                : Double.NaN;

        V6DeepLearningProfile.V6DeepLearningScore dlScore = params.dlEnabled()
                ? deepLearningProfile.score(deepLearningFeatureRow(candles, close, trend, ruleScale, signalIndex), trend2)
                : V6DeepLearningProfile.V6DeepLearningScore.neutral();
        double scoreModifier = Double.isFinite(dlScore.scoreModifier()) ? dlScore.scoreModifier() : 1.0;
        double effectiveScore = Double.isFinite(ruleScore) ? ruleScore * scoreModifier : Double.NaN;

        double buyCutoff = params.buyCutoff().doubleValue();
        double sellCutoff = params.sellCutoff().doubleValue();
        double currentRatio = StrategyMath.currentPositionRatio(position);
        boolean buySetup = trend2 > 0.0 && effectiveScore >= buyCutoff;
        boolean sellSetup = trend2 < 0.0 && effectiveScore >= sellCutoff && currentRatio > POSITION_EPSILON;
        boolean shouldSell = sellSetup;
        boolean shouldBuy = buySetup && !sellSetup && currentRatio < FULL_POSITION_RATIO - POSITION_EPSILON;

        SignalAction action = StrategyMath.resolveAction(shouldBuy, shouldSell);
        BigDecimal targetPositionRatio = BigDecimal.valueOf(StrategyMath.targetRatio(action, currentRatio));

        List<StrategyDiagnostic> diagnostics = List.of(
                StrategyDiagnostic.number("trend2.value", "Trend2", trend2),
                StrategyDiagnostic.number("trend2.score", "Trend2 Score", ruleScore),
                StrategyDiagnostic.number("trend2.effective_score", "Trend2 Effective Score", effectiveScore),
                StrategyDiagnostic.number("trend2.ema_component", "Trend2 EMA Component", trend.emaComponent()[signalIndex]),
                StrategyDiagnostic.number("trend2.ma_component", "Trend2 MA Component", trend.maComponent()[signalIndex]),
                StrategyDiagnostic.number("trend2.macd_component", "Trend2 MACD Component", trend.macdComponent()[signalIndex]),
                StrategyDiagnostic.number("trend2.momentum_component", "Trend2 Momentum Component", trend.momentumComponent()[signalIndex]),
                StrategyDiagnostic.number("trend2.rule_scale", "Trend2 Rule Scale", ruleScale),
                StrategyDiagnostic.number("trend2.buy_cutoff", "Trend2 Buy Cutoff", buyCutoff),
                StrategyDiagnostic.number("trend2.sell_cutoff", "Trend2 Sell Cutoff", sellCutoff),
                StrategyDiagnostic.number("dl.probability", "DL Probability", dlScore.probability()),
                StrategyDiagnostic.number("dl.model_side", "DL Model Side", dlScore.modelSide()),
                StrategyDiagnostic.number("dl.confidence", "DL Confidence", dlScore.confidence()),
                StrategyDiagnostic.number("dl.agreement", "DL Agreement", dlScore.agreement()),
                StrategyDiagnostic.number("dl.score_modifier", "DL Score Modifier", scoreModifier)
        );

        return new StrategyEvaluation(
                new StrategySignalDecision(action, resolveSignalReason(action, buySetup, sellSetup), targetPositionRatio),
                diagnostics
        );
    }

    private String resolveSignalReason(SignalAction action, boolean buySetup, boolean sellSetup) {
        if (action == SignalAction.BUY) {
            return "BUY_TREND2_POSITIVE";
        }
        if (action == SignalAction.SELL) {
            return "SELL_TREND2_NEGATIVE";
        }
        if (buySetup) {
            return "SETUP_BUY_TREND2";
        }
        if (sellSetup) {
            return "SETUP_SELL_TREND2";
        }
        return "NONE";
    }

    private Trend2Components trend2Components(double[] close) {
        double[] ema12 = ewmAdjustFalseMinPeriods(close, 12);
        double[] ema26 = ewmAdjustFalseMinPeriods(close, 26);
        double[] ma10 = StrategyMath.movingAverage(close, 10);
        double[] ma20 = StrategyMath.movingAverage(close, 20);
        MacdComponents macd = macd(close);
        double[] ret5 = returnLag(close, 5);
        double[] ret10 = returnLag(close, 10);

        int n = close.length;
        double[] emaComponent = fillNaN(n);
        double[] maComponent = fillNaN(n);
        double[] macdComponent = fillNaN(n);
        double[] momentumComponent = fillNaN(n);
        double[] trend2 = fillNaN(n);
        for (int i = 0; i < n; i++) {
            double price = close[i];
            if (!Double.isFinite(price) || price <= 0.0) {
                continue;
            }
            double emaValue = gap(price, ema12[i]) + (0.7 * gap(price, ema26[i]));
            double maValue = gap(price, ma10[i]) + gap(price, ma20[i]);
            double macdValue = safe(macd.line()[i]) + safe(macd.signal()[i]);
            double momentumValue = 0.5 * (safe(ret5[i]) + safe(ret10[i]));
            emaComponent[i] = emaValue;
            maComponent[i] = maValue;
            macdComponent[i] = macdValue;
            momentumComponent[i] = momentumValue;
            trend2[i] = emaValue + maValue + macdValue + momentumValue;
        }
        return new Trend2Components(emaComponent, maComponent, macdComponent, momentumComponent, trend2);
    }

    private MacdComponents macd(double[] close) {
        double[] ema12 = ewmAdjustFalseMinPeriods(close, 12);
        double[] ema26 = ewmAdjustFalseMinPeriods(close, 26);
        double[] rawLine = fillNaN(close.length);
        for (int i = 0; i < close.length; i++) {
            if (Double.isFinite(ema12[i]) && Double.isFinite(ema26[i])) {
                rawLine[i] = ema12[i] - ema26[i];
            }
        }

        double[] signalRaw = ewmAdjustFalseMinPeriods(rawLine, 9);
        double[] line = fillNaN(close.length);
        double[] signal = fillNaN(close.length);
        for (int i = 0; i < close.length; i++) {
            if (Double.isFinite(rawLine[i]) && Double.isFinite(close[i]) && close[i] > 0.0) {
                line[i] = rawLine[i] / close[i];
            }
            if (Double.isFinite(signalRaw[i]) && Double.isFinite(close[i]) && close[i] > 0.0) {
                signal[i] = signalRaw[i] / close[i];
            }
        }
        return new MacdComponents(line, signal);
    }

    private double[] ewmAdjustFalseMinPeriods(double[] values, int length) {
        double[] out = fillNaN(values.length);
        if (length <= 0) {
            return out;
        }

        double alpha = 2.0 / (length + 1.0);
        double ema = Double.NaN;
        int finiteCount = 0;
        for (int i = 0; i < values.length; i++) {
            double value = values[i];
            if (!Double.isFinite(value)) {
                continue;
            }
            ema = Double.isFinite(ema) ? (alpha * value) + ((1.0 - alpha) * ema) : value;
            finiteCount++;
            if (finiteCount >= length) {
                out[i] = ema;
            }
        }
        return out;
    }

    private double[] returnLag(double[] close, int lag) {
        double[] out = fillNaN(close.length);
        for (int i = lag; i < close.length; i++) {
            double previous = close[i - lag];
            double current = close[i];
            if (Double.isFinite(previous) && Double.isFinite(current) && previous > 0.0) {
                out[i] = (current / previous) - 1.0;
            }
        }
        return out;
    }

    /**
     * 딥러닝 forward pass용 14개 피처 행을 만든다. {@code evergreen_research/models/v6.py}의
     * {@code _dl_feature_row}를 그대로 옮긴 것이다(순서와 NaN 의미가 정확히 일치해야 한다).
     */
    private double[] deepLearningFeatureRow(
            List<OhlcvCandle> candles,
            double[] close,
            Trend2Components trend,
            double ruleScale,
            int index
    ) {
        OhlcvCandle bar = candles.get(index);
        double previousVolume = index > 0 ? candles.get(index - 1).volume() : Double.NaN;
        double trend2 = trend.trend2()[index];
        double rangePct = Double.isFinite(bar.close()) && bar.close() > 0.0
                ? (bar.high() - bar.low()) / bar.close()
                : 0.0;
        double signedScore = Double.isFinite(trend2) && ruleScale > 0.0 ? trend2 / ruleScale : 0.0;
        return new double[] {
                gap(bar.close(), bar.open()),
                rangePct,
                gap(bar.volume(), previousVolume),
                returnLagAt(close, index, 1),
                returnLagAt(close, index, 2),
                returnLagAt(close, index, 3),
                returnLagAt(close, index, 5),
                returnLagAt(close, index, 10),
                safe(trend.emaComponent()[index]),
                safe(trend.maComponent()[index]),
                safe(trend.macdComponent()[index]),
                safe(trend.momentumComponent()[index]),
                safe(trend2),
                signedScore
        };
    }

    private double returnLagAt(double[] close, int index, int lag) {
        if (index < lag) {
            return Double.NaN;
        }
        double previous = close[index - lag];
        double current = close[index];
        if (Double.isFinite(previous) && Double.isFinite(current) && previous > 0.0) {
            return (current / previous) - 1.0;
        }
        return Double.NaN;
    }

    private double gap(double current, double anchor) {
        if (!Double.isFinite(current) || !Double.isFinite(anchor) || anchor <= 0.0) {
            return 0.0;
        }
        return (current / anchor) - 1.0;
    }

    private double safe(double value) {
        return Double.isFinite(value) ? value : 0.0;
    }

    private double[] fillNaN(int size) {
        double[] values = new double[size];
        Arrays.fill(values, Double.NaN);
        return values;
    }

    private record Trend2Components(
            double[] emaComponent,
            double[] maComponent,
            double[] macdComponent,
            double[] momentumComponent,
            double[] trend2
    ) {
    }

    private record MacdComponents(
            double[] line,
            double[] signal
    ) {
    }
}
