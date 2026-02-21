package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.function.Function;
import lombok.RequiredArgsConstructor;
import lombok.extern.slf4j.Slf4j;
import org.nowstart.evergreen.data.dto.UpbitAccountResponse;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.UpbitFeignClient;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

@Slf4j
@Service
@RefreshScope
@RequiredArgsConstructor
public class TradingPositionSyncService {

    private final UpbitFeignClient upbitFeignClient;
    private final PositionRepository positionRepository;
    private final TradingProperties tradingProperties;

    @Transactional
    public void syncPositions(List<String> markets) {
        if (tradingProperties.executionMode() != ExecutionMode.LIVE || markets == null || markets.isEmpty()) {
            return;
        }

        List<UpbitAccountResponse> accounts = upbitFeignClient.getAccounts();
        Map<String, UpbitAccountResponse> accountByCurrency = accounts == null
                ? Map.of()
                : accounts.stream()
                .filter(account -> account != null && account.currency() != null && !account.currency().isBlank())
                .collect(java.util.stream.Collectors.toMap(
                        account -> account.currency().toUpperCase(Locale.ROOT),
                        Function.identity(),
                        (left, right) -> left
                ));

        for (String market : markets) {
            syncSingleMarket(market, accountByCurrency);
        }
    }

    private void syncSingleMarket(String market, Map<String, UpbitAccountResponse> accountByCurrency) {
        String assetCurrency = resolveAssetCurrency(market);
        if (assetCurrency.isBlank()) {
            return;
        }

        UpbitAccountResponse account = accountByCurrency.get(assetCurrency);
        BigDecimal qty = resolveTotalQty(account);
        boolean hasPosition = qty.compareTo(tradingProperties.minPositionQty()) > 0;
        BigDecimal avgPrice = hasPosition ? parseDecimal(account == null ? null : account.avg_buy_price()) : BigDecimal.ZERO;

        TradingPosition position = positionRepository.findBySymbol(market).orElseGet(() -> TradingPosition.builder()
                .symbol(market)
                .qty(BigDecimal.ZERO)
                .avgPrice(BigDecimal.ZERO)
                .state(PositionState.FLAT)
                .build());

        position.setQty(qty);
        position.setAvgPrice(avgPrice);
        position.setState(hasPosition ? PositionState.LONG : PositionState.FLAT);
        positionRepository.save(position);

        log.info(
                "event=position_sync market={} asset={} qty={} avg_price={} state={}",
                market,
                assetCurrency,
                qty,
                avgPrice,
                position.getState()
        );
    }

    private String resolveAssetCurrency(String market) {
        if (market == null) {
            return "";
        }

        String normalized = market.trim().toUpperCase(Locale.ROOT);
        int delimiterIndex = normalized.indexOf('-');
        if (delimiterIndex < 0 || delimiterIndex >= normalized.length() - 1) {
            return "";
        }
        return normalized.substring(delimiterIndex + 1);
    }

    private BigDecimal resolveTotalQty(UpbitAccountResponse account) {
        if (account == null) {
            return BigDecimal.ZERO;
        }
        return parseDecimal(account.balance()).add(parseDecimal(account.locked()));
    }

    private BigDecimal parseDecimal(String value) {
        if (value == null || value.isBlank()) {
            return BigDecimal.ZERO;
        }
        try {
            return new BigDecimal(value);
        } catch (NumberFormatException e) {
            return BigDecimal.ZERO;
        }
    }
}
