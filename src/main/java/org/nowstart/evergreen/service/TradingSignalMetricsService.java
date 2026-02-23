package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.util.List;
import lombok.RequiredArgsConstructor;
import org.nowstart.evergreen.data.dto.TradingExecutionMetrics;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;

@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingSignalMetricsService {

    private final TradingOrderRepository tradingOrderRepository;
    private final TradingProperties tradingProperties;

    public double resolveUnrealizedReturnPct(boolean hasPosition, double closePrice, double avgPrice) {
        if (!hasPosition || !Double.isFinite(closePrice) || !Double.isFinite(avgPrice) || avgPrice <= 0.0) {
            return Double.NaN;
        }
        return ((closePrice / avgPrice) - 1.0) * 100.0;
    }

    public TradingExecutionMetrics resolveExecutionMetrics(String market) {
        List<TradingOrder> filledOrders = tradingOrderRepository.findBySymbolAndModeAndStatusOrderByCreatedAtAsc(
                market,
                tradingProperties.executionMode(),
                OrderStatus.FILLED
        );
        if (filledOrders.isEmpty()) {
            return TradingExecutionMetrics.empty();
        }

        double positionQty = 0.0;
        double avgCost = 0.0;
        double realizedPnlKrw = 0.0;
        double realizedCostKrw = 0.0;
        int tradeCount = 0;
        int winCount = 0;
        int lossCount = 0;
        double winSumPct = 0.0;
        double lossSumAbsPct = 0.0;
        double tradeReturnSumPct = 0.0;

        double equityCurve = 1.0;
        double peakEquityCurve = 1.0;
        double maxDrawdownPct = 0.0;
        double roundTripPnlKrw = 0.0;
        double roundTripCostKrw = 0.0;

        for (TradingOrder order : filledOrders) {
            if (order == null || order.getSide() == null) {
                continue;
            }

            double qty = toPositiveDouble(order.getExecutedVolume());
            double price = toPositiveDouble(order.getAvgExecutedPrice());
            double fee = toNonNegativeDouble(order.getFeeAmount());
            if (!Double.isFinite(qty) || !Double.isFinite(price) || qty <= 0.0 || price <= 0.0) {
                continue;
            }

            if (order.getSide() == OrderSide.BUY) {
                double newQty = positionQty + qty;
                if (newQty > 0.0) {
                    double currentCostBasis = avgCost * positionQty;
                    double buyCostWithFee = (price * qty) + fee;
                    avgCost = (currentCostBasis + buyCostWithFee) / newQty;
                    positionQty = newQty;
                }
                continue;
            }

            double sellQty = Math.min(positionQty, qty);
            if (sellQty <= 0.0 || avgCost <= 0.0) {
                continue;
            }

            // When exchange data drifts (e.g. oversell correction), apply fee proportionally to matched quantity.
            double effectiveFee = sellQty < qty ? fee * (sellQty / qty) : fee;
            double proceedsAfterFee = (price * sellQty) - effectiveFee;
            double costBasis = avgCost * sellQty;
            double pnl = proceedsAfterFee - costBasis;
            realizedPnlKrw += pnl;
            realizedCostKrw += costBasis;
            roundTripPnlKrw += pnl;
            roundTripCostKrw += costBasis;

            positionQty = Math.max(0.0, positionQty - sellQty);
            if (positionQty == 0.0) {
                double tradeReturnPct = roundTripCostKrw > 0.0
                        ? (roundTripPnlKrw / roundTripCostKrw) * 100.0
                        : Double.NaN;
                if (Double.isFinite(tradeReturnPct)) {
                    tradeCount++;
                    tradeReturnSumPct += tradeReturnPct;
                    if (tradeReturnPct > 0.0) {
                        winCount++;
                        winSumPct += tradeReturnPct;
                    } else if (tradeReturnPct < 0.0) {
                        lossCount++;
                        lossSumAbsPct += Math.abs(tradeReturnPct);
                    }

                    equityCurve *= (1.0 + (tradeReturnPct / 100.0));
                    if (equityCurve > peakEquityCurve) {
                        peakEquityCurve = equityCurve;
                    }
                    if (peakEquityCurve > 0.0) {
                        double drawdown = ((equityCurve / peakEquityCurve) - 1.0) * 100.0;
                        if (drawdown < maxDrawdownPct) {
                            maxDrawdownPct = drawdown;
                        }
                    }
                }
                avgCost = 0.0;
                roundTripPnlKrw = 0.0;
                roundTripCostKrw = 0.0;
            }
        }

        double realizedReturnPct = realizedCostKrw > 0.0
                ? (realizedPnlKrw / realizedCostKrw) * 100.0
                : Double.NaN;
        double winRatePct = tradeCount > 0 ? (winCount * 100.0) / tradeCount : Double.NaN;
        double avgWinPct = winCount > 0 ? winSumPct / winCount : Double.NaN;
        double avgLossPct = lossCount > 0 ? lossSumAbsPct / lossCount : Double.NaN;
        double rrRatio = (Double.isFinite(avgWinPct) && Double.isFinite(avgLossPct) && avgLossPct > 0.0)
                ? avgWinPct / avgLossPct
                : Double.NaN;
        double expectancyPct = tradeCount > 0
                ? tradeReturnSumPct / tradeCount
                : Double.NaN;
        double maxDrawdownForMetric = tradeCount > 0 ? maxDrawdownPct : Double.NaN;

        return new TradingExecutionMetrics(
                realizedPnlKrw,
                realizedReturnPct,
                maxDrawdownForMetric,
                tradeCount,
                winRatePct,
                avgWinPct,
                avgLossPct,
                rrRatio,
                expectancyPct
        );
    }

    public double sanitizeMetricForLog(double value) {
        if (!Double.isFinite(value)) {
            return 0.0;
        }
        return value == -0.0 ? 0.0 : value;
    }

    private double toPositiveDouble(BigDecimal value) {
        if (value == null) {
            return Double.NaN;
        }
        double v = value.doubleValue();
        return v > 0.0 ? v : Double.NaN;
    }

    private double toNonNegativeDouble(BigDecimal value) {
        if (value == null) {
            return 0.0;
        }
        double v = value.doubleValue();
        return Math.max(v, 0.0);
    }

}
