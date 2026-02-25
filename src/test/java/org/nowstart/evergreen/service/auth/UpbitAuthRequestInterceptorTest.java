package org.nowstart.evergreen.service.auth;

import static org.assertj.core.api.Assertions.assertThat;

import feign.RequestTemplate;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Base64;
import java.util.Collection;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import org.junit.jupiter.api.Test;

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

    @Test
    void apply_usesCanonicalizedJsonBodyForQueryHash() {
        UpbitAuthRequestInterceptor interceptor = new UpbitAuthRequestInterceptor(new UpbitJwtSigner("access", "secret"));
        RequestTemplate template = new RequestTemplate();
        template.method("POST");
        template.uri("/v1/orders");
        template.body("{\"b\":2,\"a\":1}".getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8);

        interceptor.apply(template);

        String payload = decodePayload(headerValue(template, "Authorization").replace("Bearer ", ""));
        assertThat(payload).contains("\"query_hash_alg\":\"SHA512\"");
        assertThat(payload).contains(sha512Hex("a=1&b=2"));
    }

    @Test
    void apply_usesRawBodyWhenBodyIsInvalidJson() {
        UpbitAuthRequestInterceptor interceptor = new UpbitAuthRequestInterceptor(new UpbitJwtSigner("access", "secret"));
        RequestTemplate template = new RequestTemplate();
        template.method("POST");
        template.uri("/v1/orders");
        template.body("not-json".getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8);

        interceptor.apply(template);

        String payload = decodePayload(headerValue(template, "Authorization").replace("Bearer ", ""));
        assertThat(payload).contains(sha512Hex("not-json"));
    }

    @Test
    void apply_handlesMultiValueAndNullQueryValues() {
        UpbitAuthRequestInterceptor interceptor = new UpbitAuthRequestInterceptor(new UpbitJwtSigner("access", "secret"));
        RequestTemplate template = new RequestTemplate();
        template.method("GET");
        template.uri("/v1/orders");

        Map<String, Collection<String>> queryMap = new LinkedHashMap<>();
        queryMap.put("states", List.of("wait", "done"));
        queryMap.put("market", List.of("KRW-BTC"));
        queryMap.put("empty", List.of());
        queryMap.put("mixed", new ArrayList<>(Arrays.asList("open", null)));
        queryMap.put("nullOnly", new ArrayList<>(Collections.singletonList(null)));
        template.queries(queryMap);

        interceptor.apply(template);

        String payload = decodePayload(headerValue(template, "Authorization").replace("Bearer ", ""));
        assertThat(payload).contains(sha512Hex("market=KRW-BTC&mixed=open&states=wait&states=done"));
    }

    @Test
    void apply_ignoresNullValueFromJsonBodyDuringCanonicalization() {
        UpbitAuthRequestInterceptor interceptor = new UpbitAuthRequestInterceptor(new UpbitJwtSigner("access", "secret"));
        RequestTemplate template = new RequestTemplate();
        template.method("POST");
        template.uri("/v1/orders");
        template.body("{\"a\":1,\"b\":null}".getBytes(StandardCharsets.UTF_8), StandardCharsets.UTF_8);

        interceptor.apply(template);

        String payload = decodePayload(headerValue(template, "Authorization").replace("Bearer ", ""));
        assertThat(payload).contains(sha512Hex("a=1"));
    }

    private String headerValue(RequestTemplate template, String key) {
        return template.headers().get(key).iterator().next();
    }

    private String decodePayload(String jwt) {
        String[] parts = jwt.split("\\.");
        return new String(Base64.getUrlDecoder().decode(parts[1]), StandardCharsets.UTF_8);
    }

    private String sha512Hex(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-512");
            byte[] hashed = digest.digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(hashed.length * 2);
            for (byte b : hashed) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }
}
