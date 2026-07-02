package org.nowstart.evergreen.service.strategy.v6;

import com.fasterxml.jackson.databind.ObjectMapper;
import java.io.InputStream;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.ClassPathResource;

/**
 * 클래스패스의 {@code strategy-models/v6.json}에서 오프라인 학습된 v6 MLP 프로파일을 로드한다.
 *
 * <p>주의: Java는 번들의 {@code params.ruleScale}을 읽지 않는다 — 규칙 score와 DL의
 * {@code trend2_signed_score} 피처 모두 {@code evergreen.trading.v6.rule-scale}(config)만 쓴다.
 * 따라서 config의 rule-scale은 반드시 이 모델을 학습할 때의 rule_scale과 동일해야 한다
 * (번들의 {@code provenance.ruleScale}로 대조 가능). 불일치 시 라이브 피처 분포가 학습과 어긋난다.
 *
 * <p>리소스가 없거나 그 안에서 DL이 비활성화돼 있으면 {@link V6DeepLearningProfile#disabled()}
 * 프로파일을 제공한다. 그래야 앱이 계속 부팅되고 v6가 순수 규칙 결정으로 폴백한다
 * (score modifier 1.0). 리소스는 노트북/REPL에서 다음으로 재생성한다:
 * {@code evergreen_lab.strategies.v6_trend2.export_v6("src/main/resources/strategy-models/v6.json", bars)}.
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
            // 존재하지만 읽을 수 없는 경우는 배포 버그다: 조용히 규칙만으로 거래하지 말고 빠르게 실패한다.
            // (위의 '리소스 없음'은 의도된 규칙 전용 경로이며 그대로 우아하게 폴백한다.)
            throw new IllegalStateException(
                    "v6 model resource " + MODEL_RESOURCE + " is present but unreadable/corrupt; "
                            + "refusing to start with a silently-disabled deep-learning gate.", ex);
        }
    }
}
