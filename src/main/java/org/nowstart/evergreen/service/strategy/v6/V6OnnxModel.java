package org.nowstart.evergreen.service.strategy.v6;

import ai.onnxruntime.NodeInfo;
import ai.onnxruntime.OnnxTensor;
import ai.onnxruntime.OrtEnvironment;
import ai.onnxruntime.OrtException;
import ai.onnxruntime.OrtSession;
import ai.onnxruntime.TensorInfo;
import java.util.Collections;
import lombok.extern.slf4j.Slf4j;

/**
 * ONNX Runtime으로 v6 딥러닝 게이트를 실행하는 {@link V6DeepLearningModel} 구현.
 *
 * <p>{@code v6.onnx} 그래프에는 표준화(center/scale)+clip+relu+relu+sigmoid가 모두 baking돼 있어,
 * 입력 {@code features}(N, D) raw 피처를 넣으면 출력 {@code probability}(N,)를 그대로 얻는다.
 * {@code evergreen_lab/strategies/v6_trend2.py}의 {@code _export_onnx}가 이 그래프를 만들고,
 * numpy {@code _dl_scores}와 부동소수 오차(~1e-16) 이내로 일치함이 검증됐다.
 *
 * <p>모델 구조(은닉층 크기/깊이)가 커져도 입출력 계약이 같으면 이 클래스는 바뀌지 않는다.
 * 입력 이름·차원은 세션 메타데이터에서 읽으므로 14→32→12→1 같은 특정 구조에 묶이지 않는다.
 *
 * <p>스레드 안전성: ONNX Runtime의 {@link OrtSession#run}은 동시 호출에 안전하며, 각 인퍼런스는
 * 자체 텐서를 만들어 즉시 닫으므로 별도 동기화 없이 여러 스레드에서 호출할 수 있다.
 * float64 텐서를 넣어 numpy와 같은 배정밀도로 계산한다.
 */
@Slf4j
public final class V6OnnxModel implements V6DeepLearningModel {

    private final OrtEnvironment environment;
    private final OrtSession session;
    private final String inputName;
    private final int inputDim;

    /**
     * ONNX 모델 바이트로부터 세션을 만든다. 모델을 열 수 없으면(손상/미지원) 예외를 던진다 —
     * 조용히 규칙 전용으로 폴백하지 않는다(리소스 부재 폴백은 {@link V6ModelConfig}가 처리).
     */
    public V6OnnxModel(byte[] modelBytes) {
        if (modelBytes == null || modelBytes.length == 0) {
            throw new IllegalArgumentException("v6 ONNX model bytes are empty");
        }
        try {
            this.environment = OrtEnvironment.getEnvironment();
            // SessionOptions는 AutoCloseable 네이티브 핸들이다. createSession이 옵션을 복사하므로
            // 생성 직후 닫아 (모델 재로드/반복 테스트 시 누적되는) 네이티브 자원 누수를 막는다.
            try (OrtSession.SessionOptions options = new OrtSession.SessionOptions()) {
                this.session = environment.createSession(modelBytes, options);
            }
            // v6는 단일 입력·단일 출력 텐서 계약이다. positional 접근(첫 입력/출력)을 신뢰하기 전에
            // 이 계약을 검증해, 잘못된 export가 다른 텐서를 조용히 먹이는 것을 막는다.
            validateSingleTensorIo(session);
            this.inputName = session.getInputNames().iterator().next();
            this.inputDim = resolveInputDim(session);
        } catch (OrtException ex) {
            throw new IllegalStateException("failed to load v6 ONNX model into an ONNX Runtime session", ex);
        }
    }

    @Override
    public boolean enabled() {
        return true;
    }

    @Override
    public int inputDim() {
        return inputDim;
    }

    @Override
    public double probability(double[] rawFeatureRow) {
        if (rawFeatureRow == null) {
            return Double.NaN;
        }
        if (inputDim > 0 && rawFeatureRow.length != inputDim) {
            return Double.NaN;
        }
        double[][] batch = {rawFeatureRow};
        try (OnnxTensor input = OnnxTensor.createTensor(environment, batch);
                OrtSession.Result result = session.run(Collections.singletonMap(inputName, input))) {
            return firstValue(result.get(0).getValue());
        } catch (OrtException ex) {
            throw new IllegalStateException("v6 ONNX inference failed", ex);
        }
    }

    @Override
    public void close() {
        try {
            session.close();
        } catch (Exception ex) {
            // 종료 경로이므로 예외를 삼키되, 진단을 위해 로그는 남긴다.
            // environment는 프로세스 전역 싱글턴이라 여기서 닫지 않는다.
            log.warn("failed to close v6 ONNX session cleanly during shutdown", ex);
        }
    }

    /** v6는 단일 입력·단일 출력 텐서 계약이다. 다르면(잘못된/예상 밖 export) 즉시 실패한다. */
    private static void validateSingleTensorIo(OrtSession session) throws OrtException {
        int numInputs = session.getInputNames().size();
        int numOutputs = session.getOutputNames().size();
        if (numInputs != 1 || numOutputs != 1) {
            throw new IllegalStateException(
                    "v6 ONNX must expose exactly 1 input and 1 output, but has "
                            + numInputs + " inputs / " + numOutputs + " outputs");
        }
    }

    /** 세션 입력 텐서의 마지막 차원을 입력 피처 개수로 본다. 동적(-1)/미상이면 음수(검증 생략). */
    private static int resolveInputDim(OrtSession session) throws OrtException {
        NodeInfo node = session.getInputInfo().values().iterator().next();
        if (node.getInfo() instanceof TensorInfo tensorInfo) {
            long[] shape = tensorInfo.getShape();
            if (shape.length >= 1) {
                long last = shape[shape.length - 1];
                if (last > 0) {
                    return (int) last;
                }
            }
        }
        return -1;
    }

    /**
     * (batch,) 또는 (batch,1) 확률 출력에서 첫 원소를 꺼낸다. float 텐서도 방어적으로 처리한다.
     *
     * <p>입력 shape 불일치(예상된 NaN)는 인퍼런스 전에 걸러지므로, 여기 도달한 출력은 항상 유효한
     * 확률 배열이어야 한다. 예상치 못한/빈 출력 타입은 모델 계약 위반이므로 조용히 NaN(→규칙 전용)으로
     * 폴백하지 않고 크게 실패해, 잘못된 export가 라이브에서 영구 규칙 전용으로 퇴행하는 것을 막는다.
     */
    private static double firstValue(Object value) {
        if (value instanceof double[] arr && arr.length > 0) {
            return arr[0];
        }
        if (value instanceof float[] arr && arr.length > 0) {
            return arr[0];
        }
        if (value instanceof double[][] arr && arr.length > 0 && arr[0].length > 0) {
            return arr[0][0];
        }
        if (value instanceof float[][] arr && arr.length > 0 && arr[0].length > 0) {
            return arr[0][0];
        }
        throw new IllegalStateException(
                "v6 ONNX produced an unexpected/empty probability output: "
                        + (value == null ? "null" : value.getClass().getName()));
    }
}
