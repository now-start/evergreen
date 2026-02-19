package org.nowstart.evergreen.repository;

import org.nowstart.evergreen.data.entity.ConfigVersion;
import org.springframework.data.jpa.repository.JpaRepository;

import java.util.Optional;

public interface ConfigVersionRepository extends JpaRepository<ConfigVersion, String> {

    Optional<ConfigVersion> findByVersion(String version);

    Optional<ConfigVersion> findTopByOrderByCreatedAtDesc();
}
