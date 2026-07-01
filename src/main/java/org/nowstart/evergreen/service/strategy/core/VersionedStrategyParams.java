package org.nowstart.evergreen.service.strategy.core;

/**
 * 어느 전략 버전에 속하는지 선언하는 전략 파라미터.
 */
public interface VersionedStrategyParams extends StrategyParams {

    String version();
}
