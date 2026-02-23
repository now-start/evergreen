package org.nowstart.evergreen.service;

import java.math.BigDecimal;
import java.util.Locale;
import lombok.RequiredArgsConstructor;
import org.nowstart.evergreen.data.dto.CoexistenceStatusDto;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.repository.PositionRepository;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class CoexistenceStatusService {

    private final PositionRepository positionRepository;
    private final TradingOrderGuardService tradingOrderGuardService;

    public CoexistenceStatusDto resolveStatus(String market) {
        String normalizedMarket = normalizeMarket(market);
        TradingPosition totalPosition = positionRepository.findBySymbol(normalizedMarket).orElse(null);

        BigDecimal totalQty = safe(totalPosition == null ? null : totalPosition.getQty());

        TradingOrderGuardService.GuardDecision guardDecision = tradingOrderGuardService.evaluate(normalizedMarket);

        return new CoexistenceStatusDto(
                normalizedMarket,
                totalQty,
                guardDecision.hasExternalOpenOrder(),
                guardDecision.blocked(),
                guardDecision.blocked() ? guardDecision.reason() : null,
                guardDecision.externalOpenOrderCount(),
                totalPosition == null ? null : totalPosition.getUpdatedAt()
        );
    }

    private String normalizeMarket(String market) {
        if (market == null) {
            return "";
        }
        return market.trim().toUpperCase(Locale.ROOT);
    }

    private BigDecimal safe(BigDecimal value) {
        return value == null ? BigDecimal.ZERO : value;
    }
}
