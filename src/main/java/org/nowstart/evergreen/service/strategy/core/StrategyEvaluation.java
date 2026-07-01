package org.nowstart.evergreen.service.strategy.core;

import java.util.List;

/**
 * 전략 평가 한 번의 불변(immutable) 출력.
 *
 * <p>{@link #decision()}은 실행 가능한 신호 결정(매수/매도/보류 사유)을 담고,
 * {@link #diagnostics()}는 로깅/대시보드용 전략별 설명 가능(explainability) 지표를 담는다.
 *
 * @param decision    평가된 캔들에 대한 최종 신호 결정
 * @param diagnostics 전략이 방출한 선택적 진단 값들
 */
public record StrategyEvaluation(
        StrategySignalDecision decision,
        List<StrategyDiagnostic> diagnostics
) {

    public StrategyEvaluation {
        if (decision == null) {
            throw new IllegalArgumentException("decision is required");
        }
        diagnostics = diagnostics == null ? List.of() : List.copyOf(diagnostics);
    }
}
