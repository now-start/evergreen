package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.data.dto.TradingSignalQualityStats;
import org.nowstart.evergreen.data.dto.TradingSignalVolatilityResult;
import org.nowstart.evergreen.data.type.MarketRegime;
import org.springframework.stereotype.Service;

@Slf4j
@Service
public class TradingSignalLogService {

    public void logTicker(
            String market,
            TradingDayCandleDto signalCandle,
            double livePrice,
            MarketRegime currentRegime,
            boolean buySignal,
            boolean sellSignal,
            String signalReason
    ) {
        log.info(
                "event=ticker_price market={} ts={} live_price={} close={} regime={} buy_signal={} sell_signal={} signal_reason={}",
                market,
                signalCandle.timestamp(),
                sanitizeMetricForLog(livePrice),
                signalCandle.close(),
                currentRegime,
                buySignal,
                sellSignal,
                signalReason
        );
    }

    public void logCandleSignal(TradingSignalLogContext context) {
        TradingExecutionMetrics executionMetrics = context.executionMetrics();
        TradingSignalQualityStats signalQuality = context.signalQuality();
        TradingSignalVolatilityResult volatility = context.volatility();
        int signalIndex = context.signalIndex();

        log.info(
                "event=candle_signal market={} ts={} close={} live_price={} regime={} prev_regime={} regime_anchor={} regime_upper={} regime_lower={} atr={} atr_trail_multiplier={} atr_trail_stop={} has_position={} position_qty={} position_avg_price={} total_qty={} unrealized_return_pct={} realized_pnl_krw={} realized_return_pct={} max_drawdown_pct={} trade_count={} trade_win_rate_pct={} trade_avg_win_pct={} trade_avg_loss_pct={} trade_rr_ratio={} trade_expectancy_pct={} signal_quality_1d_avg_pct={} signal_quality_3d_avg_pct={} signal_quality_7d_avg_pct={} volatility_is_high={} atr_price_ratio={} vol_percentile={} buy_signal={} sell_signal={} signal_reason={}",
                context.market(),
                context.signalCandle().timestamp(),
                context.signalCandle().close(),
                sanitizeMetricForLog(context.livePrice()),
                context.currentRegime(),
                context.prevRegime(),
                context.regimeAnchorValue(),
                context.regimeUpperValue(),
                context.regimeLowerValue(),
                context.atrValue(),
                context.atrMultiplier(),
                context.trailStopPrice(),
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
                sanitizeMetricForLog(signalQuality.avg1dPct()),
                sanitizeMetricForLog(signalQuality.avg3dPct()),
                sanitizeMetricForLog(signalQuality.avg7dPct()),
                volatility.isHigh()[signalIndex],
                sanitizeMetricForLog(volatility.atrPriceRatio()[signalIndex]),
                sanitizeMetricForLog(volatility.percentile()[signalIndex]),
                context.buySignal(),
                context.sellSignal(),
                context.signalReason()
        );
    }

    private double sanitizeMetricForLog(double value) {
        if (!Double.isFinite(value)) {
            return 0.0;
        }
        return value == -0.0 ? 0.0 : value;
    }

    public record TradingSignalLogContext(
            String market,
            TradingDayCandleDto signalCandle,
            double livePrice,
            MarketRegime currentRegime,
            MarketRegime prevRegime,
            double regimeAnchorValue,
            double regimeUpperValue,
            double regimeLowerValue,
            double atrValue,
            double atrMultiplier,
            double trailStopPrice,
            boolean hasPosition,
            BigDecimal positionQty,
            BigDecimal positionAvgPrice,
            BigDecimal totalQty,
            double unrealizedReturnPct,
            TradingExecutionMetrics executionMetrics,
            TradingSignalQualityStats signalQuality,
            TradingSignalVolatilityResult volatility,
            int signalIndex,
            boolean buySignal,
            boolean sellSignal,
            String signalReason
    ) {
    }
}
