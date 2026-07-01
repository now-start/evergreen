package org.nowstart.evergreen.service.strategy.v6;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.SignalAction;
import org.nowstart.evergreen.service.strategy.core.StrategyDiagnostic;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyInput;

/**
 * 골든 마스터 패리티: Java v6 엔진(규칙 + MLP 게이트)은 같은 캔들 시리즈에 대해
 * {@code strategy-models/v6-golden.json}에 기록된 Python v6 신호를 그대로 재현해야 한다.
 * 픽스처는 {@code evergreen_research.model_export.export_v6(out, golden_path=...)}로 생성된다.
 *
 * <p>액션·목표비율·사유는 정확히 일치해야 하고, MLP forward-pass 값(dl 확률, effective score)은
 * 부동소수 허용오차(float tolerance) 이내로 일치해야 한다.
 */
class V6StrategyParityTest {

    private static final String FIXTURE = "/strategy-models/v6-golden.json";
    private static final double PROBABILITY_TOLERANCE = 1e-6;
    private static final double SCORE_TOLERANCE = 1e-6;
    private static final double TARGET_TOLERANCE = 1e-9;

    private V6ModelBundle bundle;
    private List<OhlcvCandle> candles;
    private V6StrategyEngine engine;
    private V6StrategyOverrides params;

    @BeforeEach
    void setUp() throws Exception {
        try (InputStream input = getClass().getResourceAsStream(FIXTURE)) {
            assertThat(input).as("golden fixture %s present", FIXTURE).isNotNull();
            bundle = new ObjectMapper().readValue(input, V6ModelBundle.class);
        }
        candles = new ArrayList<>();
        for (V6ModelBundle.Candle candle : bundle.candles()) {
            candles.add(new OhlcvCandle(
                    Instant.parse(candle.timestamp()),
                    candle.open(),
                    candle.high(),
                    candle.low(),
                    candle.close(),
                    candle.volume()));
        }
        engine = new V6StrategyEngine(bundle.toProfile());
        params = new V6StrategyOverrides(
                BigDecimal.valueOf(bundle.params().ruleScale()),
                BigDecimal.valueOf(bundle.params().buyCutoff()),
                BigDecimal.valueOf(bundle.params().sellCutoff()),
                true);
    }

    @Test
    void deepLearningProfile_loadsExpectedShape() {
        V6DeepLearningProfile profile = bundle.toProfile();
        assertThat(profile.enabled()).isTrue();
        assertThat(profile.inputDim()).isEqualTo(14);
        assertThat(profile.featureNames()).hasSize(14);
        assertThat(profile.b1()).hasSize(32);
        assertThat(profile.b2()).hasSize(12);
    }

    @Test
    void javaSignalsMatchPythonGoldenMaster() {
        int compared = 0;
        for (V6ModelBundle.ExpectedSignal expected : bundle.expectedSignals()) {
            int index = expected.index();
            if (index < 1) {
                continue; // Java 엔진은 signalIndex를 [1, size-1] 범위에서 평가한다
            }
            PositionSnapshot position = new PositionSnapshot(
                    expected.currentOpen(), 0.0, null, expected.currentOpen());
            StrategyEvaluation evaluation = engine.evaluate(
                    new StrategyInput<>(candles, index, position, params));

            assertThat(evaluation.decision().action())
                    .as("action at index %d", index)
                    .isEqualTo(SignalAction.valueOf(expected.action()));
            assertThat(evaluation.decision().signalReason())
                    .as("reason at index %d", index)
                    .isEqualTo(expected.signalReason());
            if (expected.targetPositionRatio() != null) {
                assertThat(evaluation.decision().targetPositionRatio().doubleValue())
                        .as("target at index %d", index)
                        .isCloseTo(expected.targetPositionRatio(), within(TARGET_TOLERANCE));
            }
            if (expected.dlProbability() != null) {
                assertThat(diagnostic(evaluation, "dl.probability"))
                        .as("dl probability at index %d", index)
                        .isCloseTo(expected.dlProbability(), within(PROBABILITY_TOLERANCE));
            }
            if (expected.effectiveScore() != null) {
                assertThat(diagnostic(evaluation, "trend2.effective_score"))
                        .as("effective score at index %d", index)
                        .isCloseTo(expected.effectiveScore(), within(SCORE_TOLERANCE));
            }
            compared++;
        }
        assertThat(compared).as("bars compared").isGreaterThan(300);
    }

    private double diagnostic(StrategyEvaluation evaluation, String key) {
        return evaluation.diagnostics().stream()
                .filter(item -> key.equals(item.key()))
                .mapToDouble(StrategyDiagnostic::value)
                .findFirst()
                .orElse(Double.NaN);
    }
}
