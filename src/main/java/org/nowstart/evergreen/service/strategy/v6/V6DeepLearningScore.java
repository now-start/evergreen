package org.nowstart.evergreen.service.strategy.v6;

/**
 * 한 바에 대한 v6 딥러닝 게이트 score. 확률(probability)만 있으면 rule 방향과의 합의 여부로
 * effective score modifier를 유도한다. 확률의 출처(ONNX Runtime 인퍼런스 등)와 무관하게
 * 이 modifier 계산은 항상 동일하다 — {@code evergreen_lab/strategies/v6_trend2.py}의
 * {@code _dl_scores}가 내는 modifier와 비트 수준으로 같아야 한다(Java-Python 패리티).
 *
 * <p>{@code score_modifier = agreement * (1 + 0.05 * confidence)},
 * {@code confidence = |p - 0.5| * 2}, {@code agreement = (modelSide == ruleSide) ? 1.0 : 0.94}.
 */
public record V6DeepLearningScore(
        double probability,
        int modelSide,
        double confidence,
        double agreement,
        double scoreModifier
) {

    private static final double DISAGREEMENT_FACTOR = 0.94;
    private static final double CONFIDENCE_WEIGHT = 0.05;

    /** DL이 비활성이거나 확률이 유효하지 않을 때 쓰는 중립 score(modifier 1.0으로 기여 없음). */
    public static V6DeepLearningScore neutral() {
        return new V6DeepLearningScore(Double.NaN, 0, Double.NaN, 1.0, 1.0);
    }

    /**
     * 확률과 rule 방향(trend2)으로부터 score를 유도한다. 확률이 유한하지 않으면 중립 score를 반환한다.
     * {@code _dl_scores}의 modifier 계산을 그대로 옮긴 것이다.
     */
    public static V6DeepLearningScore of(double probability, double trend2) {
        if (!Double.isFinite(probability)) {
            return neutral();
        }
        int modelSide = probability >= 0.5 ? 1 : -1;
        int ruleSide = trend2 > 0.0 ? 1 : (trend2 < 0.0 ? -1 : 0);
        double confidence = Math.abs(probability - 0.5) * 2.0;
        double agreement = modelSide == ruleSide ? 1.0 : DISAGREEMENT_FACTOR;
        double scoreModifier = agreement * (1.0 + (CONFIDENCE_WEIGHT * confidence));
        return new V6DeepLearningScore(probability, modelSide, confidence, agreement, scoreModifier);
    }
}
