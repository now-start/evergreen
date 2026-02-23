package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.anyString;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
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
        when(positionDriftSnapshotRepository.save(any(PositionDriftSnapshot.class))).thenAnswer(invocation -> invocation.getArgument(0));
        when(positionDriftSnapshotRepository.findTopBySymbolOrderByCapturedAtDesc(anyString())).thenReturn(Optional.empty());
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
}
