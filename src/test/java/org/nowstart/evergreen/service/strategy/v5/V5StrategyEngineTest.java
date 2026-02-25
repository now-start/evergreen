package org.nowstart.evergreen.service.strategy.v5;

import static org.assertj.core.api.Assertions.assertThat;

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
}
