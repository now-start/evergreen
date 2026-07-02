package org.nowstart.evergreen.service.strategy.v6;

import static org.assertj.core.api.Assertions.assertThat;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.temporal.ChronoUnit;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.SignalAction;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyInput;

class V6StrategyEngineTest {

    private final V6StrategyEngine engine = new V6StrategyEngine(V6DeepLearningProfile.disabled());
    private final V6StrategyOverrides params = new V6StrategyOverrides(
            new BigDecimal("0.01"),
            new BigDecimal("0.055"),
            new BigDecimal("0.25")
    );

    @Test
    void metadata_usesMinute240AndWarmupForTrend2Rule() {
        assertThat(engine.version()).isEqualTo("v6");
        assertThat(engine.candleIntervalKey(params)).isEqualTo("minute_240");
        assertThat(engine.requiredWarmupCandles(params)).isEqualTo(35);
    }

    @Test
    void evaluate_buysOnPositiveTrend2WhenNotFullyInvested() {
        List<OhlcvCandle> candles = trendingCandles(100.0, 1.01, 60);

        StrategyEvaluation evaluation = engine.evaluate(new StrategyInput<>(
                candles,
                candles.size() - 1,
                PositionSnapshot.EMPTY,
                params
        ));

        assertThat(evaluation.decision().action()).isEqualTo(SignalAction.BUY);
        assertThat(evaluation.decision().signalReason()).isEqualTo("BUY_TREND2_POSITIVE");
        assertThat(evaluation.decision().targetPositionRatio()).isEqualByComparingTo("1.0");
        assertThat(findDiagnostic(evaluation, "trend2.value")).isPositive();
        assertThat(findDiagnostic(evaluation, "trend2.score")).isFinite();
    }

    @Test
    void evaluate_sellsOnlyWhenNegativeTrend2AndPositionExists() {
        List<OhlcvCandle> candles = trendingCandles(200.0, 0.99, 60);
        PositionSnapshot position = new PositionSnapshot(
                1.0,
                210.0,
                candles.getFirst().timestamp(),
                1.0
        );

        StrategyEvaluation evaluation = engine.evaluate(new StrategyInput<>(
                candles,
                candles.size() - 1,
                position,
                params
        ));

        assertThat(evaluation.decision().action()).isEqualTo(SignalAction.SELL);
        assertThat(evaluation.decision().signalReason()).isEqualTo("SELL_TREND2_NEGATIVE");
        assertThat(evaluation.decision().targetPositionRatio()).isEqualByComparingTo("0.0");
    }

    @Test
    void evaluate_doesNotSellWhenThereIsNoPosition() {
        List<OhlcvCandle> candles = trendingCandles(200.0, 0.99, 60);

        StrategyEvaluation evaluation = engine.evaluate(new StrategyInput<>(
                candles,
                candles.size() - 1,
                PositionSnapshot.EMPTY,
                params
        ));

        assertThat(evaluation.decision().action()).isEqualTo(SignalAction.HOLD);
        assertThat(evaluation.decision().signalReason()).isEqualTo("NONE");
        assertThat(evaluation.decision().targetPositionRatio()).isEqualByComparingTo("0.0");
    }

    private List<OhlcvCandle> trendingCandles(double startPrice, double multiplier, int count) {
        List<OhlcvCandle> candles = new ArrayList<>();
        Instant start = Instant.parse("2026-01-01T00:00:00Z");
        double price = startPrice;
        for (int i = 0; i < count; i++) {
            price *= multiplier;
            double open = price * 0.999;
            double high = Math.max(open, price) * 1.003;
            double low = Math.min(open, price) * 0.997;
            candles.add(new OhlcvCandle(
                    start.plus(4L * i, ChronoUnit.HOURS),
                    open,
                    high,
                    low,
                    price,
                    1000.0 + i
            ));
        }
        return candles;
    }

    private double findDiagnostic(StrategyEvaluation evaluation, String key) {
        return evaluation.diagnostics().stream()
                .filter(item -> key.equals(item.key()))
                .mapToDouble(item -> item.value())
                .findFirst()
                .orElse(Double.NaN);
    }
}
