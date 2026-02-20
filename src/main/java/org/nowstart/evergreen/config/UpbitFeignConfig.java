package org.nowstart.evergreen.config;

import feign.RequestInterceptor;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.service.auth.UpbitAuthRequestInterceptor;
import org.nowstart.evergreen.service.auth.UpbitJwtSigner;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class UpbitFeignConfig {

    @Bean
    @RefreshScope
    public UpbitJwtSigner upbitJwtSigner(TradingProperties tradingProperties) {
        return new UpbitJwtSigner(tradingProperties.accessKey(), tradingProperties.secretKey());
    }

    @Bean
    @RefreshScope
    public RequestInterceptor upbitAuthRequestInterceptor(UpbitJwtSigner upbitJwtSigner) {
        return new UpbitAuthRequestInterceptor(upbitJwtSigner);
    }
}
