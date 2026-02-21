package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import org.nowstart.evergreen.data.dto.CreateOrderRequest;
import org.nowstart.evergreen.data.exception.TradingApiException;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.TradeOrderType;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;

@Service
public class OrderRequestValidationService {

    public void validate(CreateOrderRequest request) {
        if (request.side() == OrderSide.BUY && request.orderType() == TradeOrderType.MARKET_SELL) {
            throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_order", "BUY side cannot use MARKET_SELL");
        }

        if (request.side() == OrderSide.SELL && request.orderType() == TradeOrderType.MARKET_BUY) {
            throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_order", "SELL side cannot use MARKET_BUY");
        }

        if (request.orderType() == TradeOrderType.LIMIT) {
            if (request.quantity() == null || request.price() == null) {
                throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_order", "LIMIT order requires quantity and price");
            }
            return;
        }

        if (request.orderType() == TradeOrderType.MARKET_BUY) {
            if (request.mode() == ExecutionMode.PAPER
                    && (request.quantity() == null || request.quantity().compareTo(BigDecimal.ZERO) <= 0)) {
                throw new TradingApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY,
                        "invalid_order",
                        "PAPER MARKET_BUY requires quantity to simulate execution price"
                );
            }
            if (request.mode() == ExecutionMode.PAPER
                    && (request.price() == null || request.price().compareTo(BigDecimal.ZERO) <= 0)) {
                throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_order", "MARKET_BUY requires price(notional)");
            }
            if (request.mode() == ExecutionMode.LIVE
                    && request.price() != null
                    && request.price().compareTo(BigDecimal.ZERO) <= 0) {
                throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_order", "MARKET_BUY price must be greater than zero");
            }
            return;
        }

        if (request.orderType() == TradeOrderType.MARKET_SELL) {
            if (request.quantity() == null || request.quantity().compareTo(BigDecimal.ZERO) <= 0) {
                throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_order", "MARKET_SELL requires quantity");
            }
            if (request.mode() == ExecutionMode.PAPER
                    && (request.price() == null || request.price().compareTo(BigDecimal.ZERO) <= 0)) {
                throw new TradingApiException(
                        HttpStatus.UNPROCESSABLE_ENTITY,
                        "invalid_order",
                        "PAPER MARKET_SELL requires price"
                );
            }
            return;
        }

        throw new TradingApiException(HttpStatus.UNPROCESSABLE_ENTITY, "invalid_order", "Unsupported order type");
    }
}
