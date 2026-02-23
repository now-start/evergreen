package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.CoexistenceStatusDto;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.repository.PositionRepository;

@ExtendWith(MockitoExtension.class)
class CoexistenceStatusServiceTest {

    @Mock
    private PositionRepository positionRepository;
    @Mock
    private TradingOrderGuardService tradingOrderGuardService;

    @Test
    void resolveStatus_returnsPositionBreakdownAndExternalOrderFlag() {
        CoexistenceStatusService service = new CoexistenceStatusService(
                positionRepository,
                tradingOrderGuardService
        );

        TradingPosition total = TradingPosition.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("0.12"))
                .avgPrice(new BigDecimal("50000000"))
                .build();
        total.setUpdatedAt(Instant.parse("2026-02-21T01:00:00Z"));

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(total));
        when(tradingOrderGuardService.evaluate("KRW-BTC"))
                .thenReturn(new TradingOrderGuardService.GuardDecision(true, TradingOrderGuardService.GUARD_REASON_EXTERNAL_OPEN_ORDER, true, 1));

        CoexistenceStatusDto status = service.resolveStatus("krw-btc");

        assertThat(status.market()).isEqualTo("KRW-BTC");
        assertThat(status.totalQty()).isEqualByComparingTo("0.12");
        assertThat(status.hasExternalOpenOrder()).isTrue();
        assertThat(status.guardBlocked()).isTrue();
        assertThat(status.guardBlockedReason()).isEqualTo(TradingOrderGuardService.GUARD_REASON_EXTERNAL_OPEN_ORDER);
        assertThat(status.externalOpenOrderCount()).isEqualTo(1);
        assertThat(status.lastPositionSyncAt()).isEqualTo(Instant.parse("2026-02-21T01:00:00Z"));
    }

    @Test
    void resolveStatus_skipsExternalOrderLookupWhenGuardDisabled() {
        CoexistenceStatusService service = new CoexistenceStatusService(
                positionRepository,
                tradingOrderGuardService
        );

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.empty());
        when(tradingOrderGuardService.evaluate("KRW-BTC"))
                .thenReturn(new TradingOrderGuardService.GuardDecision(false, TradingOrderGuardService.GUARD_REASON_NONE, false, 0));

        CoexistenceStatusDto status = service.resolveStatus("KRW-BTC");

        assertThat(status.hasExternalOpenOrder()).isFalse();
        assertThat(status.guardBlocked()).isFalse();
        assertThat(status.guardBlockedReason()).isNull();
        verify(tradingOrderGuardService).evaluate("KRW-BTC");
    }
}
