package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.Fill;
import org.springframework.data.jpa.repository.JpaRepository;

public interface FillRepository extends JpaRepository<Fill, Fill.FillKey> {
}
