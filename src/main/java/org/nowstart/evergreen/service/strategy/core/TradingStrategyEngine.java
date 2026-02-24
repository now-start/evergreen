package org.nowstart.evergreen.service.strategy.core;

/**
 * Versioned strategy engine contract.
 *
 * @param <P> parameter type consumed by the strategy implementation
 */
public interface TradingStrategyEngine<P extends StrategyParams> {

    /**
     * Returns the strategy version key (for example {@code v5}, {@code v6}).
     */
    String version();

    /**
     * Returns the runtime class for parameter binding/validation.
     */
    Class<P> parameterType();

    /**
     * Returns the minimum candle history length required to evaluate one signal.
     */
    int requiredWarmupCandles(P params);

    /**
     * Evaluates one signal point and returns decision plus diagnostics.
     *
     * <p>Implementations should place strategy-specific explainability values in
     * {@link StrategyEvaluation#diagnostics()} using {@link StrategyDiagnostic} factories.
     */
    StrategyEvaluation evaluate(StrategyInput<P> input);
}
