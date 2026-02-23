package org.nowstart.evergreen.data.property;

import jakarta.validation.constraints.DecimalMax;
import jakarta.validation.constraints.DecimalMin;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.springframework.boot.context.properties.ConfigurationProperties;
import org.springframework.boot.context.properties.bind.DefaultValue;
import org.springframework.validation.annotation.Validated;

@Validated
@ConfigurationProperties(prefix = "evergreen.trading")
public record TradingProperties(
        // 업비트 REST API 기본 URL
        @NotBlank @DefaultValue("https://api.upbit.com") String baseUrl,
        // 업비트 Access Key (실거래/조회 인증용)
        @DefaultValue("") String accessKey,
        // 업비트 Secret Key (JWT 서명용 비밀키)
        @DefaultValue("") String secretKey,
        // 수수료율(예: 0.0005 = 0.05%)
        @DecimalMin("0") @DefaultValue("0.0005") BigDecimal feeRate,
        // 스케줄러 실행 주기
        @NotNull @DefaultValue("30s") Duration interval,
        // 주문 실행 모드(LIVE 또는 PAPER)
        @NotNull @DefaultValue("LIVE") ExecutionMode executionMode,
        // 자동매매 대상 마켓 목록
        @NotNull @DefaultValue("KRW-BTC") List<String> markets,
        // 일봉 기준 신호 계산에 사용할 캔들 수
        @Positive @DefaultValue("400") int candleCount,
        // 실시간 미완성 캔들 제외 여부(백테스트 정합성)
        @DefaultValue("true") boolean closedCandleOnly,
        // 레짐 판단용 EMA 길이
        @Positive @DefaultValue("120") int regimeEmaLen,
        // ATR 기간
        @Positive @DefaultValue("18") int atrPeriod,
        // 저변동성 구간 ATR 배수
        @DecimalMin("0") @DefaultValue("2.0") BigDecimal atrMultLowVol,
        // 고변동성 구간 ATR 배수
        @DecimalMin("0") @DefaultValue("3.0") BigDecimal atrMultHighVol,
        // 변동성 백분위 계산 lookback 길이
        @Positive @DefaultValue("40") int volRegimeLookback,
        // 고변동성 판정 백분위 임계값(0~1)
        @DecimalMin(value = "0", inclusive = false) @DecimalMax("1.0") @DefaultValue("0.6") BigDecimal volRegimeThreshold,
        // 레짐 밴드 비율
        @DecimalMin("0") @DecimalMax("0.999999") @DefaultValue("0.01") BigDecimal regimeBand,
        // PAPER 모드 시그널 진입 시도 주문 금액(KRW 기준)
        @DecimalMin(value = "0", inclusive = false) @DefaultValue("100000") BigDecimal signalOrderNotional
) {
}
