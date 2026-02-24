package org.nowstart.evergreen.service.strategy.core;

/**
 * Strategy parameters that declare which strategy version they belong to.
 */
public interface VersionedStrategyParams extends StrategyParams {

    String version();
}
