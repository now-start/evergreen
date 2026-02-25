package org.nowstart.evergreen.service.strategy.v5;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyInput;

class V5StrategyEngineTest {

    private final V5StrategyEngine engine = new V5StrategyEngine();
    private final V5StrategyOverrides params = new V5StrategyOverrides(
            2,
            1,
            BigDecimal.valueOf(2.0),
            BigDecimal.valueOf(3.0),
            2,
            BigDecimal.valueOf(0.6),
            BigDecimal.ZERO
    );

    @Test
    void evaluate_returnsBuySignalOnBearToBullWithoutPosition() {
        List<OhlcvCandle> candles = List.of(
                candle("2026-01-01T00:00:00Z", 100, 101, 99, 100),
                candle("2026-01-02T00:00:00Z", 90, 91, 89, 90),
                candle("2026-01-03T00:00:00Z", 110, 111, 109, 110)
        );

        StrategyEvaluation evaluation = engine.evaluate(new StrategyInput<>(candles, 2, PositionSnapshot.EMPTY, params));

        assertThat(evaluation.decision().buySignal()).isTrue();
        assertThat(evaluation.decision().sellSignal()).isFalse();
        assertThat(evaluation.decision().signalReason()).isEqualTo("BUY_REGIME_TRANSITION");
        assertThat(findNumberDiagnostic(evaluation, "regime.anchor")).isFinite();
        assertThat(findNumberDiagnostic(evaluation, "regime.upper")).isFinite();
        assertThat(findNumberDiagnostic(evaluation, "regime.lower")).isFinite();
    }

    @Test
    void evaluate_returnsSellSignalOnBullToBearWhenPositionExists() {
        List<OhlcvCandle> candles = List.of(
                candle("2026-01-01T00:00:00Z", 100, 101, 99, 100),
                candle("2026-01-02T00:00:00Z", 120, 121, 119, 120),
                candle("2026-01-03T00:00:00Z", 90, 91, 89, 90)
        );

        PositionSnapshot position = new PositionSnapshot(1.0, 100.0, Instant.parse("2026-01-01T00:00:00Z"));
        StrategyEvaluation evaluation = engine.evaluate(new StrategyInput<>(candles, 2, position, params));

        assertThat(evaluation.decision().buySignal()).isFalse();
        assertThat(evaluation.decision().sellSignal()).isTrue();
        assertThat(evaluation.decision().signalReason()).isEqualTo("SELL_REGIME_TRANSITION");
    }

    @Test
    void evaluate_triggersTrailStopWithoutRegimeSell() {
        V5StrategyOverrides trailStopParams = new V5StrategyOverrides(
                2,
                3,
                BigDecimal.valueOf(1.0),
                BigDecimal.valueOf(1.0),
                3,
                BigDecimal.valueOf(0.99),
                BigDecimal.valueOf(0.15)
        );
        List<OhlcvCandle> candles = List.of(
                candle("2026-01-01T00:00:00Z", 100, 101, 99, 100),
                candle("2026-01-02T00:00:00Z", 101, 102, 100, 101),
                candle("2026-01-03T00:00:00Z", 102, 103, 101, 102),
                candle("2026-01-04T00:00:00Z", 120, 121, 119, 120),
                candle("2026-01-05T00:00:00Z", 110, 111, 109, 110)
        );

        PositionSnapshot position = new PositionSnapshot(1.0, 100.0, Instant.parse("2026-01-01T00:00:00Z"));
        StrategyEvaluation evaluation = engine.evaluate(new StrategyInput<>(candles, 4, position, trailStopParams));

        double trailStopPrice = findNumberDiagnostic(evaluation, "atr.trail_stop");
        assertThat(trailStopPrice).isFinite();
        assertThat(trailStopPrice).isGreaterThanOrEqualTo(candles.get(4).close());
        assertThat(evaluation.decision().sellSignal()).isTrue();
        assertThat(evaluation.decision().signalReason()).isEqualTo("SELL_TRAIL_STOP");
    }

    @Test
    void requiredWarmupCandles_returnsMaxWindow() {
        assertThat(engine.requiredWarmupCandles(params)).isEqualTo(2);
    }

    @Test
    void evaluate_throwsWhenInputIsInvalid() {
        assertThatThrownBy(() -> engine.evaluate(null))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("input, candles, and params are required");

        List<OhlcvCandle> candles = List.of(
                candle("2026-01-01T00:00:00Z", 100, 101, 99, 100),
                candle("2026-01-02T00:00:00Z", 101, 102, 100, 101)
        );
        assertThatThrownBy(() -> engine.evaluate(new StrategyInput<>(candles, 0, PositionSnapshot.EMPTY, params)))
                .isInstanceOf(IllegalArgumentException.class)
                .hasMessageContaining("signalIndex must be in [1, candles.size()-1]");
    }

    @Test
    void evaluate_acceptsNullPositionByTreatingItAsEmpty() {
        List<OhlcvCandle> candles = List.of(
                candle("2026-01-01T00:00:00Z", 100, 101, 99, 100),
                candle("2026-01-02T00:00:00Z", 90, 91, 89, 90),
                candle("2026-01-03T00:00:00Z", 110, 111, 109, 110)
        );

        StrategyEvaluation evaluation = engine.evaluate(new StrategyInput<>(candles, 2, null, params));

        assertThat(evaluation.decision().buySignal()).isTrue();
    }

    @Test
    void privateIndicators_returnNaNWhenWindowSizesAreInvalid() throws Exception {
        double[] ema = (double[]) invokePrivate(
                "exponentialMovingAverage",
                new Class<?>[] {double[].class, int.class},
                new Object[] {new double[] {1.0, 2.0}, 0}
        );
        double[] atr = (double[]) invokePrivate(
                "wilderAtr",
                new Class<?>[] {double[].class, double[].class, double[].class, int.class},
                new Object[] {new double[] {2.0}, new double[] {1.0}, new double[] {1.5}, 0}
        );

        for (double value : ema) {
            assertThat(value).isNaN();
        }
        for (double value : atr) {
            assertThat(value).isNaN();
        }
    }

    @Test
    void privateResolveRegimes_coversBearAndUnknownFallbackBranches() throws Exception {
        Object resultA = invokePrivate(
                "resolveRegimes",
                new Class<?>[] {double[].class, double[].class, double.class},
                new Object[] {new double[] {100.0, 99.0, 100.0}, new double[] {100.0, 100.0, 100.0}, 0.5}
        );
        Object resultB = invokePrivate(
                "resolveRegimes",
                new Class<?>[] {double[].class, double[].class, double.class},
                new Object[] {new double[] {101.0}, new double[] {100.0}, 0.5}
        );

        String asTextA = java.util.Arrays.toString((Object[]) resultA);
        String asTextB = java.util.Arrays.toString((Object[]) resultB);
        assertThat(asTextA).contains("UNKNOWN", "BEAR");
        assertThat(asTextB).contains("BULL");
    }

    @Test
    void privateVolatilityAndReasonHelpers_coverRemainingBranches() throws Exception {
        Object volatilityNoWindow = invokePrivate(
                "resolveVolatilityStates",
                new Class<?>[] {double[].class, double[].class, int.class, double.class},
                new Object[] {new double[] {1.0}, new double[] {100.0}, 0, 0.5}
        );
        Object volatilityMixed = invokePrivate(
                "resolveVolatilityStates",
                new Class<?>[] {double[].class, double[].class, int.class, double.class},
                new Object[] {new double[] {2.0, 1.0}, new double[] {1.0, 1.0}, 2, 0.9}
        );
        assertThat(volatilityNoWindow).isNotNull();
        assertThat(volatilityMixed).isNotNull();

        String sellBoth = (String) invokePrivate(
                "resolveSignalReason",
                new Class<?>[] {boolean.class, boolean.class, boolean.class, boolean.class, boolean.class},
                new Object[] {false, true, false, true, true}
        );
        String setupBuy = (String) invokePrivate(
                "resolveSignalReason",
                new Class<?>[] {boolean.class, boolean.class, boolean.class, boolean.class, boolean.class},
                new Object[] {false, false, true, false, false}
        );
        String setupSell = (String) invokePrivate(
                "resolveSignalReason",
                new Class<?>[] {boolean.class, boolean.class, boolean.class, boolean.class, boolean.class},
                new Object[] {false, false, false, true, false}
        );
        String none = (String) invokePrivate(
                "resolveSignalReason",
                new Class<?>[] {boolean.class, boolean.class, boolean.class, boolean.class, boolean.class},
                new Object[] {false, false, false, false, false}
        );

        assertThat(sellBoth).isEqualTo("SELL_REGIME_AND_TRAIL_STOP");
        assertThat(setupBuy).isEqualTo("SETUP_BUY");
        assertThat(setupSell).isEqualTo("SETUP_SELL");
        assertThat(none).isEqualTo("NONE");
    }

    @Test
    void privateTrailStopAndHighestClose_coverNaNAndNotFoundBranches() throws Exception {
        List<OhlcvCandle> nanCloseCandles = List.of(
                new OhlcvCandle(Instant.parse("2026-01-01T00:00:00Z"), 1.0, 1.0, 1.0, Double.NaN, 1.0)
        );
        Object trail = invokePrivate(
                "evaluateTrailStop",
                new Class<?>[] {List.class, int.class, double[].class, double.class, PositionSnapshot.class, boolean.class},
                new Object[] {nanCloseCandles, 0, new double[] {1.0}, 1.0, new PositionSnapshot(1.0, 1.0, Instant.parse("2026-01-01T00:00:00Z")),
                        true}
        );
        assertThat(trail.toString()).contains("stopPrice=NaN");

        List<OhlcvCandle> candles = List.of(
                candle("2026-01-01T00:00:00Z", 100, 101, 99, 100),
                candle("2026-01-02T00:00:00Z", 101, 102, 100, 101)
        );
        double highest = (double) invokePrivate(
                "resolveHighestCloseSinceEntry",
                new Class<?>[] {List.class, int.class, PositionSnapshot.class},
                new Object[] {candles, 1, new PositionSnapshot(1.0, 100.0, Instant.parse("2026-01-10T00:00:00Z"))}
        );
        assertThat(highest).isEqualTo(101.0);
    }

    private OhlcvCandle candle(String ts, double open, double high, double low, double close) {
        return new OhlcvCandle(Instant.parse(ts), open, high, low, close, 1000.0);
    }

    private double findNumberDiagnostic(StrategyEvaluation evaluation, String key) {
        return evaluation.diagnostics().stream()
                .filter(item -> key.equals(item.key()))
                .mapToDouble(item -> item.value())
                .findFirst()
                .orElse(Double.NaN);
    }

    private Object invokePrivate(String methodName, Class<?>[] parameterTypes, Object[] args) throws Exception {
        Method method = V5StrategyEngine.class.getDeclaredMethod(methodName, parameterTypes);
        method.setAccessible(true);
        return method.invoke(engine, args);
    }
}
