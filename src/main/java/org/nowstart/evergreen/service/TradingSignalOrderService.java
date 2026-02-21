package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.OrderDto;
import org.nowstart.evergreen.data.dto.SignalExecuteRequest;
import org.nowstart.evergreen.data.dto.TradingDayCandleDto;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.TradeOrderType;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;

@Slf4j
@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingSignalOrderService {

    private final TradingExecutionService tradingExecutionService;
    private final TradingProperties tradingProperties;
    private final TradingSignalStateService tradingSignalStateService;

    public void submitBuySignal(String market, TradingDayCandleDto signalCandle) {
        if (tradingSignalStateService.isDuplicateSignal(market, OrderSide.BUY, signalCandle.timestamp())) {
            return;
        }

        BigDecimal paperQuantity = null;
        BigDecimal orderPrice = null;
        if (tradingProperties.executionMode() == ExecutionMode.PAPER) {
            BigDecimal signalOrderNotional = tradingProperties.signalOrderNotional();
            if (signalCandle.close().compareTo(BigDecimal.ZERO) <= 0) {
                log.warn("Skipping buy signal due to non-positive close price. market={}, close={}", market, signalCandle.close());
                return;
            }

            BigDecimal quantity = signalOrderNotional.divide(signalCandle.close(), 12, RoundingMode.DOWN);
            if (quantity.compareTo(BigDecimal.ZERO) <= 0) {
                log.warn(
                        "Skipping buy signal due to non-positive paper quantity. market={}, notional={}, close={}",
                        market,
                        signalOrderNotional,
                        signalCandle.close()
                );
                return;
            }
            paperQuantity = quantity;
            orderPrice = signalOrderNotional;
        }

        SignalExecuteRequest request = new SignalExecuteRequest(
                market,
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                paperQuantity,
                orderPrice,
                tradingProperties.executionMode(),
                signalCandle.timestamp().toString()
        );

        submitSignal(market, signalCandle, OrderSide.BUY, request);
    }

    public void submitSellSignal(String market, TradingDayCandleDto signalCandle, BigDecimal positionQty) {
        if (tradingSignalStateService.isDuplicateSignal(market, OrderSide.SELL, signalCandle.timestamp())) {
            return;
        }

        if (positionQty == null || positionQty.compareTo(tradingProperties.minPositionQty()) <= 0) {
            return;
        }

        BigDecimal paperPrice = tradingProperties.executionMode() == ExecutionMode.PAPER ? signalCandle.close() : null;
        SignalExecuteRequest request = new SignalExecuteRequest(
                market,
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                positionQty,
                paperPrice,
                tradingProperties.executionMode(),
                signalCandle.timestamp().toString()
        );

        submitSignal(market, signalCandle, OrderSide.SELL, request);
    }

    private void submitSignal(String market, TradingDayCandleDto signalCandle, OrderSide side, SignalExecuteRequest request) {
        OrderDto submittedOrder = tradingExecutionService.executeSignal(request);
        double executedPrice = submittedOrder.avgExecutedPrice() == null
                ? Double.NaN
                : submittedOrder.avgExecutedPrice().doubleValue();
        double signalClose = signalCandle.close().doubleValue();
        double slippagePct = resolveSlippagePct(signalClose, executedPrice);
        double slippageBps = Double.isFinite(slippagePct) ? slippagePct * 100.0 : Double.NaN;

        log.info(
                "event=trade_execution market={} side={} signal_ts={} signal_close={} client_order_id={} order_status={} mode={} executed_price={} executed_volume={} fee_amount={} slippage_pct={} slippage_bps={}",
                market,
                side,
                signalCandle.timestamp(),
                signalClose,
                submittedOrder.clientOrderId(),
                submittedOrder.status(),
                submittedOrder.mode(),
                executedPrice,
                submittedOrder.executedVolume(),
                submittedOrder.feeAmount(),
                slippagePct,
                slippageBps
        );
        tradingSignalStateService.recordSubmittedSignal(market, side, signalCandle.timestamp());
    }

    private double resolveSlippagePct(double signalClose, double executedPrice) {
        if (!Double.isFinite(signalClose) || !Double.isFinite(executedPrice) || signalClose <= 0.0 || executedPrice <= 0.0) {
            return Double.NaN;
        }
        return ((executedPrice / signalClose) - 1.0) * 100.0;
    }
}
