package org.nowstart.evergreen.strategy.core;

public interface TradingStrategyEngine<P extends StrategyParams> {

    String version();

    Class<P> parameterType();

    int requiredWarmupCandles(P params);

    StrategyEvaluation evaluate(StrategyInput<P> input);
}
