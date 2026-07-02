package org.nowstart.evergreen.service.strategy.v6;

import java.io.IOException;
import lombok.extern.slf4j.Slf4j;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.core.io.ClassPathResource;

/**
 * 클래스패스의 v6 ONNX 모델({@code strategy-models/v6.onnx})을 로드해 ONNX Runtime 게이트
 * ({@link V6OnnxModel})를 만든다. 모델은 {@code evergreen_lab.strategies.v6_trend2.export_v6}로
 * 학습·생성하며, 별도의 JSON 매니페스트 없이 self-contained ONNX 그래프 하나만 쓴다.
 *
 * <p>주의: 규칙 score와 DL의 {@code trend2_signed_score} 피처 모두 인퍼런스에
 * {@code evergreen.trading.v6.rule-scale}(config)만 쓴다. 따라서 config의 rule-scale은 반드시
 * 이 모델을 학습할 때 해결된 rule_scale과 동일해야 한다({@code export_v6}가 학습 후 그 값을 출력하니
 * config에 반영). 불일치 시 라이브 피처 분포가 학습과 어긋난다.
 *
 * <p>폴백 규칙: 모델 리소스가 <b>없으면</b> {@link V6DeepLearningModel#disabled()}로 규칙 전용
 * 부팅한다(score modifier 1.0). 모델이 <b>있는데</b> 읽을 수 없거나 ONNX 로드에 실패하면 배포
 * 버그이므로 조용히 규칙 전용으로 거래하지 않고 빠르게 실패한다. 리소스는 노트북/REPL에서 재생성한다:
 * {@code export_v6("src/main/resources/strategy-models/v6.onnx", bars)}.
 */
@Slf4j
@Configuration
public class V6ModelConfig {

    private static final String ONNX_RESOURCE = "strategy-models/v6.onnx";

    @Bean(destroyMethod = "close")
    public V6DeepLearningModel v6DeepLearningModel() {
        ClassPathResource onnx = new ClassPathResource(ONNX_RESOURCE);
        if (!onnx.exists()) {
            log.warn("v6 ONNX model {} not found; v6 deep-learning gate disabled (rule-only).", ONNX_RESOURCE);
            return V6DeepLearningModel.disabled();
        }
        try {
            V6OnnxModel model = new V6OnnxModel(onnx.getContentAsByteArray());
            log.info("Loaded v6 ONNX gate: model={}, inputDim={}.", ONNX_RESOURCE, model.inputDim());
            return model;
        } catch (IOException ex) {
            // 존재하지만 읽을 수 없는 경우는 배포 버그다: 조용히 규칙만으로 거래하지 않고 빠르게 실패한다.
            throw new IllegalStateException(
                    "v6 ONNX model " + ONNX_RESOURCE + " is present but unreadable; "
                            + "refusing to start with a silently-disabled deep-learning gate.", ex);
        }
    }
}
