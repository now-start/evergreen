package org.nowstart.evergreen.service.strategy.core;

/**
 * 버전이 지정된(versioned) 전략 엔진 계약.
 *
 * @param <P> 전략 구현이 소비하는 파라미터 타입
 */
public interface TradingStrategyEngine<P extends StrategyParams> {

    /**
     * 전략 버전 key를 반환한다(예: {@code v5}, {@code v6}).
     */
    String version();

    /**
     * 파라미터 바인딩/검증에 쓰이는 런타임 클래스를 반환한다.
     */
    Class<P> parameterType();

    /**
     * 이 전략이 요구하는 캔들 인터벌 key를 반환한다.
     */
    default String candleIntervalKey(P params) {
        return "days";
    }

    /**
     * 신호 하나를 평가하는 데 필요한 최소 캔들 히스토리 길이를 반환한다.
     */
    int requiredWarmupCandles(P params);

    /**
     * 신호 지점 하나를 평가해 결정과 진단 값을 반환한다.
     *
     * <p>구현체는 전략별 설명 가능(explainability) 값을 {@link StrategyDiagnostic} 팩토리로
     * {@link StrategyEvaluation#diagnostics()}에 담아야 한다.
     */
    StrategyEvaluation evaluate(StrategyInput<P> input);
}
