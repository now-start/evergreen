package org.nowstart.evergreen.service.strategy.v6;

/**
 * v6 딥러닝 게이트의 확률 소스 추상화. 구현체는 raw 피처 행 하나를 받아 매수 확률을 낸다.
 * score modifier 유도는 {@link V6DeepLearningScore#of}에 위임하므로, 모델 구조가 커지거나
 * 인퍼런스 백엔드가 바뀌어도({@link V6OnnxModel}) 이 계약과 전략 엔진 코드는 그대로다(확장성).
 *
 * <p>{@link AutoCloseable}을 확장하므로 네이티브 자원을 쥔 구현체(ONNX 세션)는 Spring
 * 종료 시 자동으로 닫힌다. 리소스가 없을 때는 {@link #disabled()}로 규칙 전용 폴백한다.
 */
public interface V6DeepLearningModel extends AutoCloseable {

    /** 모델이 로드돼 실제 확률을 낼 수 있으면 true. */
    boolean enabled();

    /** 모델이 기대하는 입력 피처 개수. 동적/미상이면 음수(길이 검증 생략). */
    int inputDim();

    /**
     * 단일 raw 피처 행에 대한 매수 확률. 입력이 null이거나 학습된 입력 차원과 맞지 않으면
     * {@link Double#NaN}을 반환한다(그 경우 score는 중립이 된다).
     */
    double probability(double[] rawFeatureRow);

    /** 확률을 구해 {@link V6DeepLearningScore}로 유도한다. 확률이 NaN이면 중립 score. */
    default V6DeepLearningScore score(double[] rawFeatureRow, double trend2) {
        return V6DeepLearningScore.of(probability(rawFeatureRow), trend2);
    }

    /** 기본 구현은 자원이 없으므로 아무것도 하지 않는다. ONNX 구현이 세션을 닫도록 오버라이드한다. */
    @Override
    default void close() {
        // no-op
    }

    /** DL이 비활성이거나 리소스가 없을 때 쓰는 중립 모델(항상 확률 NaN → score modifier 1.0). */
    static V6DeepLearningModel disabled() {
        return Disabled.INSTANCE;
    }

    /** 규칙 전용 폴백 모델. */
    final class Disabled implements V6DeepLearningModel {

        private static final Disabled INSTANCE = new Disabled();

        private Disabled() {
        }

        @Override
        public boolean enabled() {
            return false;
        }

        @Override
        public int inputDim() {
            return 0;
        }

        @Override
        public double probability(double[] rawFeatureRow) {
            return Double.NaN;
        }

        @Override
        public V6DeepLearningScore score(double[] rawFeatureRow, double trend2) {
            return V6DeepLearningScore.neutral();
        }
    }
}
