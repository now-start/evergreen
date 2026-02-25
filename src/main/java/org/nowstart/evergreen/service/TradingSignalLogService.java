package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.util.Comparator;
import java.util.stream.Collectors;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.service.strategy.core.StrategyDiagnostic;
import org.nowstart.evergreen.service.strategy.core.StrategyEvaluation;
import org.springframework.stereotype.Service;

@Slf4j
@Service
public class TradingSignalLogService {

    public void logCandleSignal(TradingSignalLogContext context) {
        TradingExecutionMetrics executionMetrics = context.executionMetrics();
        StrategyEvaluation strategyEvaluation = context.strategyEvaluation();

        log.info(
                "event=candle_signal market={} strategy_version={} ts={} close={} live_price={} has_position={} position_qty={} position_avg_price={} total_qty={} unrealized_return_pct={} realized_pnl_krw={} realized_return_pct={} max_drawdown_pct={} trade_count={} trade_win_rate_pct={} trade_avg_win_pct={} trade_avg_loss_pct={} trade_rr_ratio={} trade_expectancy_pct={} buy_signal={} sell_signal={} signal_reason={} diagnostics={} diagnostics_schema={}",
                context.market(),
                context.strategyVersion(),
                context.signalCandle().timestamp(),
                context.signalCandle().close(),
                sanitizeMetricForLog(context.livePrice()),
                context.hasPosition(),
                context.positionQty(),
                context.positionAvgPrice(),
                context.totalQty(),
                sanitizeMetricForLog(context.unrealizedReturnPct()),
                sanitizeMetricForLog(executionMetrics.realizedPnlKrw()),
                sanitizeMetricForLog(executionMetrics.realizedReturnPct()),
                sanitizeMetricForLog(executionMetrics.maxDrawdownPct()),
                executionMetrics.tradeCount(),
                sanitizeMetricForLog(executionMetrics.winRatePct()),
                sanitizeMetricForLog(executionMetrics.avgWinPct()),
                sanitizeMetricForLog(executionMetrics.avgLossPct()),
                sanitizeMetricForLog(executionMetrics.rrRatio()),
                sanitizeMetricForLog(executionMetrics.expectancyPct()),
                strategyEvaluation.decision().buySignal(),
                strategyEvaluation.decision().sellSignal(),
                strategyEvaluation.decision().signalReason(),
                formatDiagnosticValues(strategyEvaluation),
                formatDiagnosticSchema(strategyEvaluation)
        );

        emitStrategyDiagnostics(context, strategyEvaluation);
    }

    private void emitStrategyDiagnostics(TradingSignalLogContext context, StrategyEvaluation strategyEvaluation) {
        for (StrategyDiagnostic diagnostic : strategyEvaluation.diagnostics()) {
            double value = sanitizeMetricForLog(diagnostic.value());

            log.info(
                    "event=strategy_diagnostic market={} strategy_version={} ts={} key={} label=\"{}\" value={} buy_signal={} sell_signal={} signal_reason={}",
                    context.market(),
                    context.strategyVersion(),
                    context.signalCandle().timestamp(),
                    diagnostic.key(),
                    escape(diagnostic.label()),
                    value,
                    strategyEvaluation.decision().buySignal(),
                    strategyEvaluation.decision().sellSignal(),
                    strategyEvaluation.decision().signalReason()
            );
        }
    }

    private String formatDiagnosticValues(StrategyEvaluation strategyEvaluation) {
        return strategyEvaluation.diagnostics().stream()
                .sorted(Comparator.comparing(StrategyDiagnostic::key))
                .map(item -> item.key() + "=" + formatDiagnosticValue(item.value()))
                .collect(Collectors.joining(", ", "{", "}"));
    }

    private String formatDiagnosticSchema(StrategyEvaluation strategyEvaluation) {
        return strategyEvaluation.diagnostics().stream()
                .sorted(Comparator.comparing(StrategyDiagnostic::key))
                .map(item -> item.key()
                        + ":{label=\"" + escape(item.label()) + "\"}")
                .collect(Collectors.joining(", ", "{", "}"));
    }

    private String formatDiagnosticValue(double value) {
        return Double.toString(sanitizeMetricForLog(value));
    }

    private String escape(String value) {
        return value.replace("\\", "\\\\").replace("\"", "\\\"");
    }

    private double sanitizeMetricForLog(double value) {
        if (!Double.isFinite(value)) {
            return 0.0;
        }
        return value == -0.0 ? 0.0 : value;
    }

    public record TradingSignalLogContext(
            String market,
            String strategyVersion,
            TradingDayCandleDto signalCandle,
            double livePrice,
            boolean hasPosition,
            BigDecimal positionQty,
            BigDecimal positionAvgPrice,
            BigDecimal totalQty,
            double unrealizedReturnPct,
            TradingExecutionMetrics executionMetrics,
            StrategyEvaluation strategyEvaluation
    ) {
    }
}
