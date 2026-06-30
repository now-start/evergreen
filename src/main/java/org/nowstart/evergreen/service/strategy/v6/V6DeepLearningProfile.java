package org.nowstart.evergreen.service.strategy.v6;

import java.util.List;

/**
 * Immutable v6 MLP profile (14 → 32 → 12 → 1) trained offline in Python and loaded from
 * {@code strategy-models/v6.json}. Java only runs the forward pass; training stays in Python
 * because the numpy RNG/dropout/quantile reductions are not bit-reproducible here.
 *
 * <p>Standardization and forward pass mirror {@code _dl_signal_scores} in
 * {@code evergreen_research/models/v6.py} exactly:
 * {@code x = clip(nan_to_num((raw - center) / scale, nan=0, posinf=8, neginf=-8), -8, 8)},
 * then {@code relu}/{@code relu}/{@code sigmoid}.
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

    /** Defensively copies the weight arrays so the profile cannot be mutated through its inputs. */
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

    /** Neutral profile: DL contributes nothing (score modifier is always 1.0). */
    public static V6DeepLearningProfile disabled() {
        return new V6DeepLearningProfile(false, List.of(), new double[0], new double[0],
                new double[0][], new double[0], new double[0][], new double[0], new double[0], 0.0);
    }

    /** Number of input features the network expects, or 0 when disabled. */
    public int inputDim() {
        return center.length;
    }

    /**
     * Forward pass for a single raw feature row. Returns {@link Double#NaN} when the profile is
     * disabled or the row length does not match the trained input dimension.
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
     * Computes the deep-learning score for the bar, mirroring {@code _dl_signal_scores}. When the
     * profile is disabled or the probability is non-finite, returns the neutral score (modifier 1.0).
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

    /** One bar's deep-learning score, mirroring {@code DeepLearningScoreV6}. */
    public record V6DeepLearningScore(
            double probability,
            int modelSide,
            double confidence,
            double agreement,
            double scoreModifier
    ) {

        /** Neutral score used when DL is disabled or unavailable. */
        public static V6DeepLearningScore neutral() {
            return new V6DeepLearningScore(Double.NaN, 0, Double.NaN, 1.0, 1.0);
        }
    }
}
