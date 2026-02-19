package org.nowstart.evergreen.backtest.model;

import java.util.List;

public record BacktestResult(
        List<BacktestRow> rows,
        BacktestSummary summary
) {}
