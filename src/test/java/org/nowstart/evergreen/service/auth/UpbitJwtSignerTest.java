package org.nowstart.evergreen.service.auth;

import org.junit.jupiter.api.Test;

import java.nio.charset.StandardCharsets;
import java.util.Base64;

import static org.assertj.core.api.Assertions.assertThat;

class UpbitJwtSignerTest {

    @Test
    void createToken_includesQueryHashWhenCanonicalQueryExists() {
        UpbitJwtSigner signer = new UpbitJwtSigner("access", "secret");

        String token = signer.createToken("market=KRW-BTC&side=bid");
        String[] parts = token.split("\\.");

        assertThat(parts).hasSize(3);

        String payloadJson = new String(Base64.getUrlDecoder().decode(parts[1]), StandardCharsets.UTF_8);
        assertThat(payloadJson).contains("\"access_key\":\"access\"");
        assertThat(payloadJson).contains("\"query_hash\"");
        assertThat(payloadJson).contains("\"query_hash_alg\":\"SHA512\"");
    }

    @Test
    void createToken_omitsQueryHashWhenCanonicalQueryMissing() {
        UpbitJwtSigner signer = new UpbitJwtSigner("access", "secret");

        String token = signer.createToken("");
        String[] parts = token.split("\\.");

        assertThat(parts).hasSize(3);

        String payloadJson = new String(Base64.getUrlDecoder().decode(parts[1]), StandardCharsets.UTF_8);
        assertThat(payloadJson).contains("\"access_key\":\"access\"");
        assertThat(payloadJson).doesNotContain("query_hash");
    }
}
