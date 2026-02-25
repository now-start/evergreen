package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.math.RoundingMode;
import java.util.List;
import java.util.UUID;
import lombok.RequiredArgsConstructor;
import org.nowstart.evergreen.data.dto.BalanceDto;
import org.nowstart.evergreen.data.dto.CreateOrderRequest;
import org.nowstart.evergreen.data.dto.OrderChanceDto;
import org.nowstart.evergreen.data.dto.OrderDto;
import org.nowstart.evergreen.data.dto.SignalExecuteRequest;
import org.nowstart.evergreen.data.dto.UpbitAccountResponse;
import org.nowstart.evergreen.data.dto.UpbitCreateOrderRequest;
import org.nowstart.evergreen.data.dto.UpbitOrderChanceResponse;
import org.nowstart.evergreen.data.dto.UpbitOrderResponse;
import org.nowstart.evergreen.data.dto.UpbitTickerResponse;
import org.nowstart.evergreen.data.entity.AuditEvent;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.exception.TradingApiException;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.TradeOrderType;
import org.nowstart.evergreen.repository.AuditEventRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.nowstart.evergreen.repository.UpbitFeignClient;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingExecutionService {

    private final UpbitFeignClient upbitFeignClient;
    private final TradingOrderRepository tradingOrderRepository;
    private final AuditEventRepository auditEventRepository;
    private final OrderReconciliationService orderReconciliationService;
    private final PaperExecutionService paperExecutionService;
    private final TradingProperties tradingProperties;
    private final OrderRequestValidationService orderRequestValidationService;
    private final TradingOrderFactory tradingOrderFactory;

    public List<BalanceDto> getBalances(String currency) {
        return upbitFeignClient.getAccounts().stream()
                .filter(account -> currency == null || currency.isBlank() || currency.equalsIgnoreCase(account.currency()))
                .map(this::toBalance)
                .toList();
    }

    public OrderChanceDto getOrderChance(String market) {
        UpbitOrderChanceResponse chance = upbitFeignClient.getOrderChance(market);
        return new OrderChanceDto(
                market,
                parseDecimal(chance.bid_fee()),
                parseDecimal(chance.ask_fee()),
                chance.bid_account() == null ? BigDecimal.ZERO : parseDecimal(chance.bid_account().balance()),
                chance.ask_account() == null ? BigDecimal.ZERO : parseDecimal(chance.ask_account().balance()),
                chance.market() == null || chance.market().max_total() == null
                        ? BigDecimal.ZERO
                        : parseDecimal(chance.market().max_total().max_total())
        );
    }

    @Transactional
    public OrderDto createOrder(CreateOrderRequest request) {
        orderRequestValidationService.validate(request);

        TradingOrder order = tradingOrderFactory.build(request);
        if (request.mode() == ExecutionMode.PAPER) {
            normalizePaperOrder(order);
            tradingOrderRepository.save(order);
            TradingOrder filled = paperExecutionService.execute(order);
            writeAudit("PAPER_ORDER_EXECUTED", "clientOrderId=" + filled.getClientOrderId());
            return toOrderDto(filled);
        }

        guardLiveOrder(order);
        tradingOrderRepository.save(order);

        UpbitCreateOrderRequest upbitRequest = toUpbitRequest(order);
        UpbitOrderResponse created = upbitFeignClient.createOrder(upbitRequest);
        TradingOrder reconciled = orderReconciliationService.reconcile(order, created);
        writeAudit("LIVE_ORDER_SUBMITTED", "clientOrderId=" + reconciled.getClientOrderId());
        return toOrderDto(reconciled);
    }

    @Transactional
    public OrderDto cancelOrder(String clientOrderId) {
        TradingOrder order = tradingOrderRepository.findByClientOrderId(clientOrderId)
                .orElseThrow(() -> new TradingApiException(HttpStatus.NOT_FOUND, "order_not_found", "Order not found"));

        if (order.getMode() == ExecutionMode.PAPER) {
            if (order.getStatus() == OrderStatus.FILLED) {
                throw new TradingApiException(HttpStatus.CONFLICT, "cannot_cancel", "Filled PAPER order cannot be canceled");
            }
            order.setStatus(OrderStatus.CANCELED);
            tradingOrderRepository.save(order);
            return toOrderDto(order);
        }

        if (order.getExchangeOrderId() == null || order.getExchangeOrderId().isBlank()) {
            throw new TradingApiException(HttpStatus.CONFLICT, "missing_exchange_id", "Order exchange uuid is missing");
        }

        UpbitOrderResponse canceled = upbitFeignClient.cancelOrder(order.getExchangeOrderId());
        TradingOrder reconciled = orderReconciliationService.reconcile(order, canceled);
        writeAudit("LIVE_ORDER_CANCELED", "clientOrderId=" + reconciled.getClientOrderId());
        return toOrderDto(reconciled);
    }

    @Transactional
    public OrderDto getOrder(String clientOrderId) {
        TradingOrder order = tradingOrderRepository.findByClientOrderId(clientOrderId)
                .orElseThrow(() -> new TradingApiException(HttpStatus.NOT_FOUND, "order_not_found", "Order not found"));

        if (order.getMode() == ExecutionMode.LIVE
                && order.getExchangeOrderId() != null
                && !order.getExchangeOrderId().isBlank()) {
            UpbitOrderResponse response = upbitFeignClient.getOrder(order.getExchangeOrderId());
            order = orderReconciliationService.reconcile(order, response);
        }

        return toOrderDto(order);
    }

    @Transactional
    public OrderDto executeSignal(SignalExecuteRequest request) {
        String signalReason = request.signalTimestamp() == null || request.signalTimestamp().isBlank()
                ? "signal"
                : "signal:" + request.signalTimestamp();

        CreateOrderRequest orderRequest = new CreateOrderRequest(
                request.market(),
                request.side(),
                request.orderType(),
                request.quantity(),
                request.price(),
                request.mode(),
                signalReason
        );
        return createOrder(orderRequest);
    }

    private void guardLiveOrder(TradingOrder order) {
        UpbitOrderChanceResponse chance = upbitFeignClient.getOrderChance(order.getSymbol());
        BigDecimal requestedNotional = estimateRequestedNotional(order, chance);
        order.setRequestedNotional(requestedNotional);
        if (order.getOrderType() == TradeOrderType.MARKET_BUY
                && safe(order.getPrice()).compareTo(BigDecimal.ZERO) <= 0) {
            order.setPrice(requestedNotional);
        }

        validateBalanceAndChance(order, chance, requestedNotional);
    }

    private void validateBalanceAndChance(TradingOrder order, UpbitOrderChanceResponse chance, BigDecimal requestedNotional) {
        if (order.getSide() == OrderSide.BUY) {
            BigDecimal balance = chance.bid_account() == null ? BigDecimal.ZERO : parseDecimal(chance.bid_account().balance());
            if (balance.compareTo(requestedNotional) < 0) {
                throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "insufficient_balance", "Insufficient KRW balance for buy order");
            }
            return;
        }

        BigDecimal askBalance = chance.ask_account() == null ? BigDecimal.ZERO : parseDecimal(chance.ask_account().balance());
        BigDecimal qty = safe(order.getQuantity());
        if (askBalance.compareTo(qty) < 0) {
            throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "insufficient_balance", "Insufficient asset balance for sell order");
        }
    }

    private BigDecimal estimateRequestedNotional(TradingOrder order, UpbitOrderChanceResponse chance) {
        BigDecimal requested = safe(order.getRequestedNotional());
        if (requested.compareTo(BigDecimal.ZERO) > 0) {
            return requested;
        }

        if (order.getOrderType() == TradeOrderType.MARKET_BUY) {
            BigDecimal balance = chance.bid_account() == null ? BigDecimal.ZERO : parseDecimal(chance.bid_account().balance());
            BigDecimal spendable = toSpendableKrw(balance);
            if (spendable.compareTo(BigDecimal.ZERO) <= 0) {
                throw new TradingApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY,
                        "insufficient_balance",
                        "Insufficient KRW balance for buy order"
                );
            }
            return spendable;
        }

        if (order.getOrderType() == TradeOrderType.MARKET_SELL) {
            BigDecimal referencePrice = safe(order.getPrice());
            if (referencePrice.compareTo(BigDecimal.ZERO) <= 0) {
                referencePrice = chance.ask_account() == null
                        ? BigDecimal.ZERO
                        : parseDecimal(chance.ask_account().avg_buy_price());
            }
            if (referencePrice.compareTo(BigDecimal.ZERO) <= 0) {
                referencePrice = fetchLatestTradePrice(order.getSymbol());
            }

            if (referencePrice.compareTo(BigDecimal.ZERO) <= 0) {
                throw new TradingApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY,
                        "missing_reference_price",
                        "Cannot estimate notional for MARKET_SELL without reference price"
                );
            }

            return safe(order.getQuantity()).multiply(referencePrice);
        }

        return requested;
    }

    private void normalizePaperOrder(TradingOrder order) {
        if (order.getOrderType() == TradeOrderType.MARKET_BUY) {
            BigDecimal qty = safe(order.getQuantity());
            if (qty.compareTo(BigDecimal.ZERO) <= 0) {
                throw new TradingApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY,
                        "invalid_order",
                        "PAPER MARKET_BUY requires quantity"
                );
            }

            BigDecimal unitPrice = safe(order.getRequestedNotional()).divide(qty, 12, RoundingMode.HALF_UP);
            order.setPrice(unitPrice);
        }
    }

    private BigDecimal fetchLatestTradePrice(String market) {
        List<UpbitTickerResponse> tickers = upbitFeignClient.getTickers(market);
        if (tickers == null || tickers.isEmpty()) {
            return BigDecimal.ZERO;
        }
        BigDecimal tradePrice = tickers.getFirst().trade_price();
        if (tradePrice == null || tradePrice.compareTo(BigDecimal.ZERO) <= 0) {
            return BigDecimal.ZERO;
        }
        return tradePrice;
    }

    private BigDecimal toSpendableKrw(BigDecimal balance) {
        BigDecimal multiplier = BigDecimal.ONE.subtract(safe(tradingProperties.feeRate()));
        if (multiplier.compareTo(BigDecimal.ZERO) <= 0 || multiplier.compareTo(BigDecimal.ONE) > 0) {
            multiplier = BigDecimal.ONE;
        }
        return safe(balance)
                .multiply(multiplier)
                .setScale(0, RoundingMode.DOWN);
    }

    private UpbitCreateOrderRequest toUpbitRequest(TradingOrder order) {
        String side = order.getSide() == OrderSide.BUY ? "bid" : "ask";

        return switch (order.getOrderType()) {
            case LIMIT -> new UpbitCreateOrderRequest(
                    order.getSymbol(),
                    side,
                    "limit",
                    stringify(order.getQuantity()),
                    stringify(order.getPrice()),
                    order.getClientOrderId()
            );
            case MARKET_BUY -> new UpbitCreateOrderRequest(
                    order.getSymbol(),
                    side,
                    "price",
                    null,
                    stringify(order.getPrice()),
                    order.getClientOrderId()
            );
            case MARKET_SELL -> new UpbitCreateOrderRequest(
                    order.getSymbol(),
                    side,
                    "market",
                    stringify(order.getQuantity()),
                    null,
                    order.getClientOrderId()
            );
        };
    }

    private void writeAudit(String type, String payload) {
        AuditEvent event = AuditEvent.builder()
                .eventId(UUID.randomUUID())
                .type(type)
                .payload(payload)
                .build();
        auditEventRepository.save(event);
    }

    private BalanceDto toBalance(UpbitAccountResponse response) {
        return new BalanceDto(
                response.currency(),
                parseDecimal(response.balance()),
                parseDecimal(response.locked()),
                parseDecimal(response.avg_buy_price()),
                response.unit_currency()
        );
    }

    private OrderDto toOrderDto(TradingOrder order) {
        return new OrderDto(
                order.getClientOrderId(),
                order.getExchangeOrderId(),
                order.getSymbol(),
                order.getSide(),
                order.getOrderType(),
                order.getMode(),
                order.getQuantity(),
                order.getPrice(),
                order.getRequestedNotional(),
                order.getExecutedVolume(),
                order.getAvgExecutedPrice(),
                order.getFeeAmount(),
                order.getStatus(),
                order.getReason()
        );
    }

    private BigDecimal parseDecimal(String value) {
        if (value == null || value.isBlank()) {
            return BigDecimal.ZERO;
        }
        return new BigDecimal(value);
    }

    private BigDecimal safe(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }

    private String stringify(BigDecimal value) {
        return value == null ? null : value.stripTrailingZeros().toPlainString();
    }
}
