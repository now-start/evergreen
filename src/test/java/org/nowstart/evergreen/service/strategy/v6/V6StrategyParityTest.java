package org.nowstart.evergreen.service.strategy.v6;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.within;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.service.strategy.core.OhlcvCandle;
import org.nowstart.evergreen.service.strategy.core.PositionSnapshot;
import org.nowstart.evergreen.service.strategy.core.SignalAction;
import org.nowstart.evergreen.service.strategy.core.StrategyDiagnostic;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.nowstart.evergreen.service.strategy.core.StrategyInput;

/**
 * 골든 마스터 패리티: Java v6 엔진(규칙 + ONNX Runtime MLP 게이트)은 같은 캔들 시리즈에 대해
 * {@code strategy-models/v6-golden.json}에 기록된 Python v6 신호를 그대로 재현해야 한다.
 * 픽스처는 {@code evergreen_lab.strategies.v6_trend2.export_v6(out, bars, golden_path=...)}로
 * 생성되며, 같은 학습 프로파일에서 나온 {@code v6-golden.onnx}(모델)와 골든 신호가 짝을 이룬다.
 *
 * <p>Java는 이 ONNX를 ONNX Runtime으로 실행해 확률을 얻고, 그 확률로 signal을 계산한다.
 * 액션·목표비율·사유는 정확히 일치해야 하고, ONNX forward-pass 값(dl 확률, effective score)은
 * 부동소수 허용오차(float tolerance) 이내로 Python numpy 값과 일치해야 한다.
 */
class V6StrategyParityTest {

    private static final String JSON_FIXTURE = "/strategy-models/v6-golden.json";
    private static final String ONNX_FIXTURE = "/strategy-models/v6-golden.onnx";
    private static final double PROBABILITY_TOLERANCE = 1e-6;
    private static final double SCORE_TOLERANCE = 1e-6;
    private static final double TARGET_TOLERANCE = 1e-9;

    private V6ModelBundle bundle;
    private List<OhlcvCandle> candles;
    private V6DeepLearningModel model;
    private V6StrategyEngine engine;
    private V6StrategyOverrides params;

    @BeforeEach
    void setUp() throws Exception {
        try (InputStream input = getClass().getResourceAsStream(JSON_FIXTURE)) {
            assertThat(input).as("golden json fixture %s present", JSON_FIXTURE).isNotNull();
            bundle = new ObjectMapper().readValue(input, V6ModelBundle.class);
        }
        byte[] onnxBytes;
        try (InputStream input = getClass().getResourceAsStream(ONNX_FIXTURE)) {
            assertThat(input).as("golden onnx fixture %s present", ONNX_FIXTURE).isNotNull();
            onnxBytes = input.readAllBytes();
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
        model = new V6OnnxModel(onnxBytes);
        engine = new V6StrategyEngine(model);
        params = new V6StrategyOverrides(
                BigDecimal.valueOf(bundle.params().ruleScale()),
                BigDecimal.valueOf(bundle.params().buyCutoff()),
                BigDecimal.valueOf(bundle.params().sellCutoff()));
    }

    @AfterEach
    void tearDown() {
        if (model != null) {
            model.close();
        }
    }

    @Test
    void deepLearningModel_loadsExpectedShape() {
        assertThat(model.enabled()).isTrue();
        assertThat(model.inputDim()).isEqualTo(14);
    }

    /**
     * ONNX 세션은 라이브에서 스케줄러 스레드와 HTTP 요청 스레드가 동시에 추론할 수 있다.
     * 여러 스레드가 같은 세션에 동시 접근해도 예외 없이, 단일 스레드와 동일한 확률을 내는지 검증한다
     * (각 호출은 자체 텐서를 만들어 닫으므로 ONNX Runtime의 run()은 동시 호출에 안전하다).
     */
    @Test
    void deepLearningModel_probabilityIsThreadSafeUnderConcurrency() throws Exception {
        double[] row = new double[model.inputDim()];
        for (int i = 0; i < row.length; i++) {
            row[i] = 0.01 * (i + 1); // 임의의 유한 피처; 결정론적 추론이면 스레드마다 동일 결과여야 한다
        }
        double expected = model.probability(row);
        assertThat(expected).as("baseline probability").isFinite();

        int totalCalls = 400;
        ExecutorService pool = Executors.newFixedThreadPool(8);
        try {
            List<Future<Double>> futures = new ArrayList<>();
            for (int i = 0; i < totalCalls; i++) {
                futures.add(pool.submit(() -> model.probability(row.clone())));
            }
            for (Future<Double> future : futures) {
                assertThat(future.get())
                        .as("concurrent probability matches single-threaded result")
                        .isEqualTo(expected);
            }
        } finally {
            pool.shutdownNow();
        }
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
