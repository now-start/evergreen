package org.nowstart.evergreen.service.strategy.v6;

import java.util.List;

/**
 * 불변(immutable) v6 MLP 프로파일 (14 → 32 → 12 → 1). Python에서 오프라인 학습해
 * {@code strategy-models/v6.json}에서 로드한다. Java는 forward pass만 수행하며, 학습은
 * Python에 남긴다(numpy의 RNG/dropout/quantile 축약이 여기서는 비트 단위로 재현되지 않으므로).
 *
 * <p>표준화(standardization)와 forward pass는 {@code evergreen_research/models/v6.py}의
 * {@code _dl_signal_scores}를 정확히 그대로 옮긴 것이다:
 * {@code x = clip(nan_to_num((raw - center) / scale, nan=0, posinf=8, neginf=-8), -8, 8)},
 * 그 뒤 {@code relu}/{@code relu}/{@code sigmoid}.
 */
public record V6DeepLearningProfile(
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

    private static final double STANDARDIZE_CLIP = 8.0;
    private static final double LOGIT_CLIP = 50.0;
    private static final double DISAGREEMENT_FACTOR = 0.94;
    private static final double CONFIDENCE_WEIGHT = 0.05;

    /** 가중치 배열을 방어적으로 복사해, 입력을 통해 프로파일이 변경되지 않도록 한다. */
    public V6DeepLearningProfile {
        featureNames = featureNames == null ? List.of() : List.copyOf(featureNames);
        center = copy(center);
        scale = copy(scale);
        w1 = copy(w1);
        b1 = copy(b1);
        w2 = copy(w2);
        b2 = copy(b2);
        w3 = copy(w3);
    }

    /** 중립 프로파일: DL이 아무 기여도 하지 않는다(score modifier가 항상 1.0). */
    public static V6DeepLearningProfile disabled() {
        return new V6DeepLearningProfile(false, List.of(), new double[0], new double[0],
                new double[0][], new double[0], new double[0][], new double[0], new double[0], 0.0);
    }

    /** 네트워크가 기대하는 입력 피처 개수, 비활성 상태면 0. */
    public int inputDim() {
        return center.length;
    }

    /**
     * 단일 raw 피처 행에 대한 forward pass. 프로파일이 비활성이거나 행 길이가 학습된 입력
     * 차원과 맞지 않으면 {@link Double#NaN}을 반환한다.
     */
    public double probability(double[] rawFeatureRow) {
        if (!enabled || rawFeatureRow == null || rawFeatureRow.length != inputDim() || inputDim() == 0) {
            return Double.NaN;
        }

        double[] x = standardize(rawFeatureRow);
        double[] a1 = reluLayer(x, w1, b1);
        double[] a2 = reluLayer(a1, w2, b2);
        double logit = b3;
        for (int m = 0; m < a2.length; m++) {
            logit += a2[m] * w3[m];
        }
        double clipped = Math.max(-LOGIT_CLIP, Math.min(LOGIT_CLIP, logit));
        return 1.0 / (1.0 + Math.exp(-clipped));
    }

    /**
     * 해당 바의 딥러닝 score를 계산한다({@code _dl_signal_scores}를 그대로 옮김). 프로파일이
     * 비활성이거나 확률이 유한하지 않으면 중립 score(modifier 1.0)를 반환한다.
     */
    public V6DeepLearningScore score(double[] rawFeatureRow, double trend2) {
        double probability = probability(rawFeatureRow);
        if (!Double.isFinite(probability)) {
            return V6DeepLearningScore.neutral();
        }
        int modelSide = probability >= 0.5 ? 1 : -1;
        int ruleSide = trend2 > 0.0 ? 1 : (trend2 < 0.0 ? -1 : 0);
        double confidence = Math.abs(probability - 0.5) * 2.0;
        double agreement = modelSide == ruleSide ? 1.0 : DISAGREEMENT_FACTOR;
        double scoreModifier = agreement * (1.0 + (CONFIDENCE_WEIGHT * confidence));
        return new V6DeepLearningScore(probability, modelSide, confidence, agreement, scoreModifier);
    }

    private double[] standardize(double[] raw) {
        double[] out = new double[raw.length];
        for (int j = 0; j < raw.length; j++) {
            double value = (raw[j] - center[j]) / scale[j];
            if (Double.isNaN(value)) {
                value = 0.0;
            } else if (value == Double.POSITIVE_INFINITY) {
                value = STANDARDIZE_CLIP;
            } else if (value == Double.NEGATIVE_INFINITY) {
                value = -STANDARDIZE_CLIP;
            }
            out[j] = Math.max(-STANDARDIZE_CLIP, Math.min(STANDARDIZE_CLIP, value));
        }
        return out;
    }

    private static double[] reluLayer(double[] input, double[][] weights, double[] biases) {
        int units = biases.length;
        double[] out = new double[units];
        for (int unit = 0; unit < units; unit++) {
            double sum = biases[unit];
            for (int i = 0; i < input.length; i++) {
                sum += input[i] * weights[i][unit];
            }
            out[unit] = Math.max(sum, 0.0);
        }
        return out;
    }

    private static double[] copy(double[] values) {
        return values == null ? new double[0] : values.clone();
    }

    private static double[][] copy(double[][] values) {
        if (values == null) {
            return new double[0][];
        }
        double[][] out = new double[values.length][];
        for (int i = 0; i < values.length; i++) {
            out[i] = values[i] == null ? new double[0] : values[i].clone();
        }
        return out;
    }

    /** 한 바의 딥러닝 score({@code DeepLearningScoreV6}를 그대로 옮김). */
    public record V6DeepLearningScore(
            double probability,
            int modelSide,
            double confidence,
            double agreement,
            double scoreModifier
    ) {

        /** DL이 비활성이거나 사용 불가일 때 쓰는 중립 score. */
        public static V6DeepLearningScore neutral() {
            return new V6DeepLearningScore(Double.NaN, 0, Double.NaN, 1.0, 1.0);
        }
    }
}
