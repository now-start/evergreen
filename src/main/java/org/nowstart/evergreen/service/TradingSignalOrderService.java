package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.OrderChanceDto;
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

    private static final int MONEY_SCALE = 0;
    private static final int QTY_SCALE = 12;

    private final TradingExecutionService tradingExecutionService;
    private final TradingProperties tradingProperties;
    private final TradingSignalStateService tradingSignalStateService;

    public void submitTargetPositionSignal(
            String market,
            TradingDayCandleDto signalCandle,
            BigDecimal positionQty,
            BigDecimal targetPositionRatio
    ) {
        if (targetPositionRatio == null || targetPositionRatio.compareTo(BigDecimal.ZERO) < 0) {
            return;
        }
        if (signalCandle.close().compareTo(BigDecimal.ZERO) <= 0) {
            log.warn("Skipping target position signal due to non-positive close price. market={}, close={}", market, signalCandle.close());
            return;
        }

        ExecutionMode mode = tradingProperties.executionMode();
        PositionTargetSizing sizing = resolvePositionTargetSizing(signalCandle, positionQty);
        BigDecimal targetPositionValue = sizing.baseValue()
                .multiply(targetPositionRatio)
                .setScale(MONEY_SCALE, RoundingMode.DOWN);
        BigDecimal valueDelta = targetPositionValue.subtract(sizing.currentPositionValue());

        if (valueDelta.compareTo(BigDecimal.ZERO) > 0) {
            BigDecimal buyNotional = valueDelta
                    .min(resolveMaxBuyNotional(market, mode))
                    .setScale(MONEY_SCALE, RoundingMode.DOWN);
            submitSizedBuySignal(market, signalCandle, buyNotional, mode);
        } else if (valueDelta.compareTo(BigDecimal.ZERO) < 0) {
            BigDecimal sellValue = valueDelta.abs();
            BigDecimal sellQty = sellValue
                    .divide(signalCandle.close(), QTY_SCALE, RoundingMode.DOWN)
                    .min(sizing.positionQty());
            submitSizedSellSignal(market, signalCandle, sellQty, mode);
        }
    }

    public void submitBuySignal(String market, TradingDayCandleDto signalCandle) {
        if (tradingSignalStateService.isDuplicateSignal(market, OrderSide.BUY, signalCandle.timestamp())) {
            return;
        }

        ExecutionMode mode = tradingProperties.executionMode();
        SignalExecuteRequest request;
        if (mode == ExecutionMode.PAPER) {
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
            request = new SignalExecuteRequest(
                    market,
                    OrderSide.BUY,
                    TradeOrderType.MARKET_BUY,
                    quantity,
                    signalOrderNotional,
                    mode,
                    signalCandle.timestamp().toString()
            );
        } else {
            // LIVE 폴백 매수는 가격을 비워 둔다; 실행 단계에서 notional을 signal-order-notional로 상한 처리한다.
            request = new SignalExecuteRequest(
                    market,
                    OrderSide.BUY,
                    TradeOrderType.MARKET_BUY,
                    null,
                    null,
                    mode,
                    signalCandle.timestamp().toString()
            );
        }

        submitSignal(market, signalCandle, OrderSide.BUY, request);
    }

    private void submitSizedBuySignal(String market, TradingDayCandleDto signalCandle, BigDecimal notional, ExecutionMode mode) {
        if (notional == null || notional.compareTo(BigDecimal.ZERO) <= 0) {
            return;
        }
        if (tradingSignalStateService.isDuplicateSignal(market, OrderSide.BUY, signalCandle.timestamp())) {
            return;
        }

        BigDecimal resolvedNotional = notional.setScale(MONEY_SCALE, RoundingMode.DOWN);
        BigDecimal quantity = mode == ExecutionMode.PAPER
                ? resolvedNotional.divide(signalCandle.close(), QTY_SCALE, RoundingMode.DOWN)
                : null;
        if (mode == ExecutionMode.PAPER && quantity.compareTo(BigDecimal.ZERO) <= 0) {
            return;
        }
        SignalExecuteRequest request = new SignalExecuteRequest(
                market,
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                quantity,
                resolvedNotional,
                mode,
                signalCandle.timestamp().toString()
        );
        submitSignal(market, signalCandle, OrderSide.BUY, request);
    }

    public void submitSellSignal(String market, TradingDayCandleDto signalCandle, BigDecimal positionQty) {
        if (tradingSignalStateService.isDuplicateSignal(market, OrderSide.SELL, signalCandle.timestamp())) {
            return;
        }

        if (positionQty == null || positionQty.compareTo(BigDecimal.ZERO) <= 0) {
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

    private void submitSizedSellSignal(
            String market,
            TradingDayCandleDto signalCandle,
            BigDecimal sellQty,
            ExecutionMode mode
    ) {
        if (tradingSignalStateService.isDuplicateSignal(market, OrderSide.SELL, signalCandle.timestamp())) {
            return;
        }

        if (sellQty.compareTo(BigDecimal.ZERO) <= 0) {
            return;
        }

        BigDecimal paperPrice = mode == ExecutionMode.PAPER ? signalCandle.close() : null;
        SignalExecuteRequest request = new SignalExecuteRequest(
                market,
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                sellQty,
                paperPrice,
                mode,
                signalCandle.timestamp().toString()
        );
        submitSignal(market, signalCandle, OrderSide.SELL, request);
    }

    private PositionTargetSizing resolvePositionTargetSizing(
            TradingDayCandleDto signalCandle,
            BigDecimal positionQty
    ) {
        BigDecimal resolvedQty = safe(positionQty);
        BigDecimal currentPositionValue = resolvedQty
                .multiply(signalCandle.close())
                .setScale(MONEY_SCALE, RoundingMode.DOWN);
        BigDecimal baseValue = safe(tradingProperties.signalOrderNotional())
                .setScale(MONEY_SCALE, RoundingMode.DOWN);
        return new PositionTargetSizing(
                resolvedQty,
                currentPositionValue,
                baseValue
        );
    }

    private BigDecimal resolveMaxBuyNotional(String market, ExecutionMode mode) {
        if (mode == ExecutionMode.PAPER) {
            return BigDecimal.valueOf(Long.MAX_VALUE);
        }

        OrderChanceDto chance = tradingExecutionService.getOrderChance(market);
        return safe(chance.bidBalance())
                .multiply(BigDecimal.ONE.subtract(safe(tradingProperties.feeRate())))
                .setScale(MONEY_SCALE, RoundingMode.DOWN);
    }

    private BigDecimal safe(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
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

    private record PositionTargetSizing(
            BigDecimal positionQty,
            BigDecimal currentPositionValue,
            BigDecimal baseValue
    ) {
    }
}
