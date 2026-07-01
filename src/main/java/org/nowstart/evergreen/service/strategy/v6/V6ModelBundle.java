package org.nowstart.evergreen.service.strategy.v6;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.util.List;

/**
 * {@code evergreen_research/model_export.py}가 내보낸 v6 모델 번들의 Jackson 뷰.
 * 프로덕션 리소스({@code strategy-models/v6.json})는 {@link #version}, {@link #params},
 * {@link #deepLearning}을 담고, 골든 테스트 픽스처는 추가로 {@link #candles}와
 * {@link #expectedSignals}를 담는다. 알 수 없는 필드는 무시하므로 하나의 DTO로 둘 다 파싱된다.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record V6ModelBundle(
        String version,
        Params params,
        DeepLearning deepLearning,
        List<Candle> candles,
        List<ExpectedSignal> expectedSignals
) {

    /** 불변 forward-pass 프로파일을 만든다. DL이 없거나 꺼져 있으면 비활성 프로파일을 반환한다. */
    public V6DeepLearningProfile toProfile() {
        if (deepLearning == null || !deepLearning.enabled()) {
            return V6DeepLearningProfile.disabled();
        }
        return new V6DeepLearningProfile(
                true,
                deepLearning.featureNames() == null ? List.of() : List.copyOf(deepLearning.featureNames()),
                deepLearning.center(),
                deepLearning.scale(),
                deepLearning.w1(),
                deepLearning.b1(),
                deepLearning.w2(),
                deepLearning.b2(),
                deepLearning.w3(),
                deepLearning.b3()
        );
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Params(
            double ruleScale,
            double buyCutoff,
            double sellCutoff
    ) {
    }

    @JsonIgnoreProperties(ignoreUnknown = true)
    public record DeepLearning(
            boolean enabled,
            List<String> featureNames,
            double[] center,
            double[] scale,
            double[][] w1,
            double[] b1,
            double[][] w2,
            double[] b2,
            double[] w3,
            double b3
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
