package org.nowstart.evergreen.service.strategy.v6;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.util.List;

/**
 * 골든 패리티 픽스처({@code strategy-models/v6-golden.json})의 Jackson 뷰. 테스트 전용 DTO다 —
 * 런타임 인퍼런스는 JSON을 쓰지 않고 ONNX 모델({@link V6OnnxModel})만 사용하므로, 이 클래스는
 * {@link V6StrategyParityTest}가 기대 신호·캔들·파라미터를 읽는 데만 쓰인다. 모델 가중치는 담지 않는다.
 * 알 수 없는 필드(version/provenance 등)는 무시한다.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record V6ModelBundle(
        Params params,
        List<Candle> candles,
        List<ExpectedSignal> expectedSignals
) {

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Params(
            double ruleScale,
            double buyCutoff,
            double sellCutoff
    ) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Candle(
            String timestamp,
            double open,
            double high,
            double low,
            double close,
            double volume
    ) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record ExpectedSignal(
            int index,
            String timestamp,
            String action,
            String signalReason,
            Double targetPositionRatio,
            double currentOpen,
            Double ruleScore,
            Double effectiveScore,
            Double dlProbability,
            Double dlScoreModifier,
            Double trend2
    ) {
    }
}
