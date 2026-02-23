package org.nowstart.evergreen.data.entity;

import jakarta.persistence.Column;
import jakarta.persistence.Entity;
import jakarta.persistence.GeneratedValue;
import jakarta.persistence.GenerationType;
import jakarta.persistence.Id;
import java.math.BigDecimal;
import java.time.Instant;
import lombok.AccessLevel;
import lombok.AllArgsConstructor;
import lombok.Builder;
import lombok.Getter;
import lombok.NoArgsConstructor;
import lombok.Setter;

@Entity
@Getter
@Setter
@Builder
@AllArgsConstructor
@NoArgsConstructor(access = AccessLevel.PROTECTED)
public class PositionDriftSnapshot {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    private String symbol;

    @Column(precision = 38, scale = 12)
    private BigDecimal totalQty;

    @Column(precision = 38, scale = 12)
    private BigDecimal managedQty;

    @Column(precision = 38, scale = 12)
    private BigDecimal externalQty;

    @Column(precision = 38, scale = 12)
    private BigDecimal driftQty;

    private boolean driftDetected;

    private Instant capturedAt;
}
