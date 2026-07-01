package org.nowstart.evergreen.service.strategy.core;

/**
 * {@link TradingStrategyEngine#evaluate(StrategyInput)} 동안 생성되는 전략별 진단(diagnostic) 값 하나.
 *
 * <p>진단 값은 관측/대시보드용이며 실행 흐름 제어용이 아니다. 전략 엔진이 진단 값을 만들어
 * {@link StrategyEvaluation}을 통해 반환한다. 이후 로깅 계층이 다음과 같이 직렬화한다:
 * <ul>
 *   <li>모든 진단 값을 {@code candle_signal.diagnostics}로</li>
 *   <li>숫자형 진단 값을 {@code event=strategy_diagnostic} 시계열로</li>
 * </ul>
 *
 * <p>전략 엔진 내부의 전형적인 사용 예:
 * <pre>{@code
 * List<StrategyDiagnostic> diagnostics = List.of(
 *         StrategyDiagnostic.number("ema.fast", "Fast EMA", emaFast),
 *         StrategyDiagnostic.number("ema.slow", "Slow EMA", emaSlow)
 * );
 *
 * return new StrategyEvaluation(
 *         new StrategySignalDecision(SignalAction.BUY, reason, BigDecimal.ONE),
 *         diagnostics
 * );
 * }</pre>
 *
 * <p>핵심 규칙:
 * <ul>
 *   <li>안정적이고 기계가 읽기 좋은 key를 사용한다(예: {@code ema.fast}, {@code signal.confidence}).</li>
 *   <li>{@code label}은 표시용 텍스트, {@code key}는 조회/그룹핑 식별자다.</li>
 * </ul>
 *
 * @param key         로그와 대시보드 조회에 쓰이는 안정적 진단 식별자
 * @param label       UI 범례/툴팁용 사람이 읽는 이름
 * @param value       숫자형 진단 값
 */
public record StrategyDiagnostic(
        String key,
        String label,
        double value
) {

    public StrategyDiagnostic {
        if (key == null || key.isBlank()) {
            throw new IllegalArgumentException("diagnostic key is required");
        }
        label = (label == null || label.isBlank()) ? key : label;
    }

    /**
     * 숫자형 진단 값을 생성한다.
     *
     * @param key 안정적이고 기계가 읽기 좋은 key
     * @param label 표시용 label
     * @param value 숫자 값
     * @return 숫자형 진단 값
     */
    public static StrategyDiagnostic number(
            String key,
            String label,
            double value
    ) {
        return new StrategyDiagnostic(key, label, value);
    }
}
