package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.verify;

import java.lang.reflect.Method;
import java.math.BigDecimal;
import java.time.Instant;
import java.util.Optional;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.entity.PositionDriftSnapshot;
import org.nowstart.evergreen.repository.PositionDriftSnapshotRepository;

@ExtendWith(MockitoExtension.class)
class PositionDriftServiceTest {

    @Mock
    private PositionDriftSnapshotRepository positionDriftSnapshotRepository;

    @BeforeEach
    void setUp() {
        lenient().when(positionDriftSnapshotRepository.save(any(PositionDriftSnapshot.class))).thenAnswer(invocation -> invocation.getArgument(0));
        lenient().when(positionDriftSnapshotRepository.findTopBySymbolOrderByCapturedAtDesc(anyString())).thenReturn(Optional.empty());
    }

    @Test
    void captureSnapshot_marksDriftWhenExternalQtyExists() {
        PositionDriftService service = new PositionDriftService(
                positionDriftSnapshotRepository
        );

        service.captureSnapshot("KRW-BTC", new BigDecimal("0.12"), new BigDecimal("0.07"));

        ArgumentCaptor<PositionDriftSnapshot> snapshotCaptor = ArgumentCaptor.forClass(PositionDriftSnapshot.class);
        verify(positionDriftSnapshotRepository).save(snapshotCaptor.capture());
        PositionDriftSnapshot snapshot = snapshotCaptor.getValue();
        assertThat(snapshot.getTotalQty()).isEqualByComparingTo("0.12");
        assertThat(snapshot.getManagedQty()).isEqualByComparingTo("0.07");
        assertThat(snapshot.getExternalQty()).isEqualByComparingTo("0.05");
        assertThat(snapshot.getDriftQty()).isEqualByComparingTo("0.05");
        assertThat(snapshot.isDriftDetected()).isTrue();
    }

    @Test
    void captureSnapshot_clearsNegativeExternalQtyAndStillMarksDriftOnMismatch() {
        PositionDriftService service = new PositionDriftService(
                positionDriftSnapshotRepository
        );

        service.captureSnapshot("KRW-BTC", new BigDecimal("0.12"), new BigDecimal("0.20"));

        ArgumentCaptor<PositionDriftSnapshot> snapshotCaptor = ArgumentCaptor.forClass(PositionDriftSnapshot.class);
        verify(positionDriftSnapshotRepository).save(snapshotCaptor.capture());
        PositionDriftSnapshot snapshot = snapshotCaptor.getValue();
        assertThat(snapshot.getExternalQty()).isEqualByComparingTo("0");
        assertThat(snapshot.getDriftQty()).isEqualByComparingTo("0.08");
        assertThat(snapshot.isDriftDetected()).isTrue();
    }

    @Test
    void shouldEmitExternalDrift_handlesNullAndDriftComparisonCases() throws Exception {
        PositionDriftService service = new PositionDriftService(positionDriftSnapshotRepository);
        Method method = PositionDriftService.class.getDeclaredMethod(
                "shouldEmitExternalDrift",
                PositionDriftSnapshot.class,
                PositionDriftSnapshot.class
        );
        method.setAccessible(true);

        PositionDriftSnapshot noDrift = snapshot("KRW-BTC", "0.1", "0.1", false);
        PositionDriftSnapshot driftA = snapshot("KRW-BTC", "0.2", "0.1", true);
        PositionDriftSnapshot driftB = snapshot("KRW-BTC", "0.2", "0.1", true);
        PositionDriftSnapshot driftChanged = snapshot("KRW-BTC", "0.3", "0.1", true);

        assertThat((boolean) method.invoke(service, null, null)).isFalse();
        assertThat((boolean) method.invoke(service, null, noDrift)).isFalse();
        assertThat((boolean) method.invoke(service, null, driftA)).isTrue();
        assertThat((boolean) method.invoke(service, noDrift, driftA)).isTrue();
        assertThat((boolean) method.invoke(service, driftA, driftB)).isFalse();
        assertThat((boolean) method.invoke(service, driftA, driftChanged)).isTrue();
    }

    private PositionDriftSnapshot snapshot(String symbol, String totalQty, String managedQty, boolean driftDetected) {
        return PositionDriftSnapshot.builder()
                .symbol(symbol)
                .totalQty(new BigDecimal(totalQty))
                .managedQty(new BigDecimal(managedQty))
                .externalQty(new BigDecimal(totalQty).subtract(new BigDecimal(managedQty)))
                .driftQty(new BigDecimal(totalQty).subtract(new BigDecimal(managedQty)).abs())
                .driftDetected(driftDetected)
                .capturedAt(Instant.parse("2026-02-21T00:00:00Z"))
                .build();
    }
}
