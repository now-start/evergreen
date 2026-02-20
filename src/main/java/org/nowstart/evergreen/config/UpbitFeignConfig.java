package org.nowstart.evergreen.config;

import feign.RequestInterceptor;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.service.auth.UpbitAuthRequestInterceptor;
import org.nowstart.evergreen.service.auth.UpbitJwtSigner;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class UpbitFeignConfig {

    @Bean
    public UpbitJwtSigner upbitJwtSigner(TradingProperties tradingProperties) {
        return new UpbitJwtSigner(tradingProperties.accessKey(), tradingProperties.secretKey());
    }

    @Bean
    public RequestInterceptor upbitAuthRequestInterceptor(UpbitJwtSigner upbitJwtSigner) {
        return new UpbitAuthRequestInterceptor(upbitJwtSigner);
    }
}
