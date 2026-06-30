package org.nowstart.evergreen.service.strategy.v6;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import java.util.List;

/**
 * Jackson view of the exported v6 model bundle written by
 * {@code evergreen_research/model_export.py}. The production resource
 * ({@code strategy-models/v6.json}) carries {@link #version}, {@link #params} and
 * {@link #deepLearning}; the golden test fixture additionally carries {@link #candles}
 * and {@link #expectedSignals}. Unknown fields are ignored so one DTO parses both.
 */
@JsonIgnoreProperties(ignoreUnknown = true)
public record V6ModelBundle(
        String version,
        Params params,
        DeepLearning deepLearning,
        List<Candle> candles,
        List<ExpectedSignal> expectedSignals
) {

    /** Builds the immutable forward-pass profile, or a disabled profile when DL is absent/off. */
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
