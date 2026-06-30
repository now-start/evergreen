package org.nowstart.evergreen.service.strategy.v6;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.ClassPathResource;

/**
 * Loads the offline-trained v6 MLP profile from {@code strategy-models/v6.json} on the classpath.
 *
 * <p>When the resource is absent or DL is disabled in it, a {@link V6DeepLearningProfile#disabled()}
 * profile is provided so the app still boots and v6 falls back to pure rule decisions
 * (score modifier 1.0). Regenerate the resource from a notebook/REPL with
 * {@code evergreen_research.model_export.export_v6("src/main/resources/strategy-models/v6.json")}.
 */
@Slf4j
@Configuration
public class V6ModelConfig {

    private static final String MODEL_RESOURCE = "strategy-models/v6.json";

    @Bean
    public V6DeepLearningProfile v6DeepLearningProfile(ObjectMapper objectMapper) {
        ClassPathResource resource = new ClassPathResource(MODEL_RESOURCE);
        if (!resource.exists()) {
            log.warn("v6 model resource {} not found; v6 deep-learning gate disabled (rule-only).", MODEL_RESOURCE);
            return V6DeepLearningProfile.disabled();
        }
        try (InputStream input = resource.getInputStream()) {
            V6ModelBundle bundle = objectMapper.readValue(input, V6ModelBundle.class);
            V6DeepLearningProfile profile = bundle.toProfile();
            if (profile.enabled()) {
                log.info("Loaded v6 deep-learning profile: inputDim={}, hidden={}/{}.",
                        profile.inputDim(), profile.b1().length, profile.b2().length);
            } else {
                log.warn("v6 model resource {} present but DL disabled; using rule-only decisions.", MODEL_RESOURCE);
            }
            return profile;
        } catch (Exception ex) {
            // Present-but-unreadable is a deploy bug: fail fast rather than silently trade rule-only.
            // (An absent resource above is the intentional rule-only path and stays graceful.)
            throw new IllegalStateException(
                    "v6 model resource " + MODEL_RESOURCE + " is present but unreadable/corrupt; "
                            + "refusing to start with a silently-disabled deep-learning gate.", ex);
        }
    }
}
