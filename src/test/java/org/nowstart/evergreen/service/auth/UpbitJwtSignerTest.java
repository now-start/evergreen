package org.nowstart.evergreen.service.auth;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;

import java.lang.reflect.Method;
import java.nio.charset.StandardCharsets;
import java.security.Provider;
import java.security.Security;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

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

    @Test
    void createToken_wrapsSigningFailureWhenSecretIsNull() {
        UpbitJwtSigner signer = new UpbitJwtSigner("access", null);

        assertThatThrownBy(() -> signer.createToken("market=KRW-BTC"))
                .isInstanceOf(IllegalStateException.class)
                .hasMessageContaining("Failed to sign JWT");
    }

    @Test
    void toJson_wrapsSerializationFailure() throws Exception {
        Method toJson = UpbitJwtSigner.class.getDeclaredMethod("toJson", Map.class);
        toJson.setAccessible(true);

        Map<String, Object> recursive = new LinkedHashMap<>();
        recursive.put("self", recursive);

        assertThatThrownBy(() -> toJson.invoke(null, recursive))
                .hasCauseInstanceOf(IllegalStateException.class)
                .hasRootCauseInstanceOf(com.fasterxml.jackson.core.JsonProcessingException.class);
    }

    @Test
    void sha512Hex_wrapsMissingAlgorithm() throws Exception {
        Method sha512Hex = UpbitJwtSigner.class.getDeclaredMethod("sha512Hex", String.class);
        sha512Hex.setAccessible(true);

        Provider[] providers = Security.getProviders();
        for (Provider provider : providers) {
            Security.removeProvider(provider.getName());
        }
        try {
            assertThatThrownBy(() -> sha512Hex.invoke(null, "abc"))
                    .hasCauseInstanceOf(IllegalStateException.class)
                    .hasRootCauseInstanceOf(java.security.NoSuchAlgorithmException.class);
        } finally {
            for (Provider provider : providers) {
                Security.addProvider(provider);
            }
        }
    }
}
