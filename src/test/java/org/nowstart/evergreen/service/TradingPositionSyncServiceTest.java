package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.UpbitAccountResponse;
import org.nowstart.evergreen.data.entity.TradingPosition;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.PositionState;
import org.nowstart.evergreen.repository.PositionRepository;
import org.nowstart.evergreen.repository.UpbitFeignClient;

@ExtendWith(MockitoExtension.class)
class TradingPositionSyncServiceTest {

    @Mock
    private UpbitFeignClient upbitFeignClient;
    @Mock
    private PositionRepository positionRepository;
    @Mock
    private PositionDriftService positionDriftService;

    @BeforeEach
    void setUp() {
        lenient().when(positionRepository.save(any(TradingPosition.class))).thenAnswer(invocation -> invocation.getArgument(0));
    }

    @Test
    void syncPositions_liveModeUpdatesPositionAndDelegatesDriftCapture() {
        TradingPositionSyncService service = createService(ExecutionMode.LIVE);
        TradingPosition existing = TradingPosition.builder()
                .symbol("KRW-BTC")
                .qty(BigDecimal.ZERO)
                .avgPrice(BigDecimal.ZERO)
                .state(PositionState.FLAT)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(existing));
        when(upbitFeignClient.getAccounts()).thenReturn(List.of(
                new UpbitAccountResponse("BTC", "0.1", "0.02", "50000000", "KRW")
        ));

        service.syncPositions(List.of("KRW-BTC"));

        ArgumentCaptor<TradingPosition> positionCaptor = ArgumentCaptor.forClass(TradingPosition.class);
        verify(positionRepository).save(positionCaptor.capture());
        TradingPosition synced = positionCaptor.getValue();
        assertThat(synced.getQty()).isEqualByComparingTo("0.12");
        assertThat(synced.getAvgPrice()).isEqualByComparingTo("50000000");
        assertThat(synced.getState()).isEqualTo(PositionState.LONG);
        verify(positionDriftService).captureSnapshot("KRW-BTC", new BigDecimal("0.12"), new BigDecimal("0.12"));
    }

    @Test
    void syncPositions_liveModeFlattensPositionWhenAssetNotFound() {
        TradingPositionSyncService service = createService(ExecutionMode.LIVE);
        TradingPosition existing = TradingPosition.builder()
                .symbol("KRW-BTC")
                .qty(new BigDecimal("0.5"))
                .avgPrice(new BigDecimal("42000000"))
                .state(PositionState.LONG)
                .build();

        when(positionRepository.findBySymbol("KRW-BTC")).thenReturn(Optional.of(existing));
        when(upbitFeignClient.getAccounts()).thenReturn(List.of(
                new UpbitAccountResponse("KRW", "100000", "0", "0", "KRW")
        ));

        service.syncPositions(List.of("KRW-BTC"));

        ArgumentCaptor<TradingPosition> captor = ArgumentCaptor.forClass(TradingPosition.class);
        verify(positionRepository).save(captor.capture());
        TradingPosition synced = captor.getValue();
        assertThat(synced.getQty()).isEqualByComparingTo("0");
        assertThat(synced.getAvgPrice()).isEqualByComparingTo("0");
        assertThat(synced.getState()).isEqualTo(PositionState.FLAT);
        verify(positionDriftService).captureSnapshot("KRW-BTC", BigDecimal.ZERO, BigDecimal.ZERO);
    }

    @Test
    void syncPositions_paperModeSkipsExchangeSync() {
        TradingPositionSyncService service = createService(ExecutionMode.PAPER);

        service.syncPositions(List.of("KRW-BTC"));

        verify(upbitFeignClient, never()).getAccounts();
        verifyNoInteractions(positionRepository);
        verifyNoInteractions(positionDriftService);
    }

    private TradingPositionSyncService createService(ExecutionMode mode) {
        TradingProperties properties = new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                mode,
                List.of("KRW-BTC"),
                400,
                true,
                120,
                18,
                new BigDecimal("2.0"),
                new BigDecimal("3.0"),
                40,
                new BigDecimal("0.6"),
                new BigDecimal("0.01"),
                new BigDecimal("100000")
        );
        return new TradingPositionSyncService(
                upbitFeignClient,
                positionRepository,
                positionDriftService,
                properties
        );
    }
}
