package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.AuditEvent;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;
import java.util.UUID;

public interface AuditEventRepository extends JpaRepository<AuditEvent, UUID> {

    Optional<AuditEvent> findByEventId(UUID eventId);
}
