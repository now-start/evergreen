package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.Fill;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.List;

public interface FillRepository extends JpaRepository<Fill, Fill.FillKey> {

    List<Fill> findByOrderClientOrderIdOrderByIdFilledAtAsc(String clientOrderId);
}
