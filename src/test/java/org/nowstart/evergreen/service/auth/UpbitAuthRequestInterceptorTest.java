package org.nowstart.evergreen.service.auth;

import feign.RequestTemplate;
import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;

class UpbitAuthRequestInterceptorTest {

    @Test
    void apply_addsHeadersAndQueryHashWhenQueryExists() {
        UpbitAuthRequestInterceptor interceptor = new UpbitAuthRequestInterceptor(new UpbitJwtSigner("access", "secret"));
        RequestTemplate template = new RequestTemplate();
        template.method("GET");
        template.uri("/v1/orders/chance");
        template.query("market", "KRW-BTC");

        interceptor.apply(template);

        String authHeader = headerValue(template, "Authorization");
        assertThat(authHeader).startsWith("Bearer ");
        assertThat(headerValue(template, "Accept")).isEqualTo("application/json");
        assertThat(headerValue(template, "User-Agent")).isEqualTo("evergreen-backtest/1.0");

        String payload = decodePayload(authHeader.replace("Bearer ", ""));
        assertThat(payload).contains("\"access_key\":\"access\"");
        assertThat(payload).contains("query_hash");
    }

    @Test
    void apply_omitsQueryHashWhenNoQueryAndNoBody() {
        UpbitAuthRequestInterceptor interceptor = new UpbitAuthRequestInterceptor(new UpbitJwtSigner("access", "secret"));
        RequestTemplate template = new RequestTemplate();
        template.method("GET");
        template.uri("/v1/accounts");

        interceptor.apply(template);

        String payload = decodePayload(headerValue(template, "Authorization").replace("Bearer ", ""));
        assertThat(payload).contains("\"access_key\":\"access\"");
        assertThat(payload).doesNotContain("query_hash");
    }

    private String headerValue(RequestTemplate template, String key) {
        return template.headers().get(key).iterator().next();
    }

    private String decodePayload(String jwt) {
        String[] parts = jwt.split("\\.");
        return new String(Base64.getUrlDecoder().decode(parts[1]), StandardCharsets.UTF_8);
    }
}
