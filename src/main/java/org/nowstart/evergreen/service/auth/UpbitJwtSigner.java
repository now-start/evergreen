package org.nowstart.evergreen.service.auth;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Base64;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.UUID;
import javax.crypto.Mac;
import javax.crypto.spec.SecretKeySpec;
import lombok.RequiredArgsConstructor;

@RequiredArgsConstructor
public class UpbitJwtSigner {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final String accessKey;
    private final String secretKey;

    public String createToken(String canonicalQuery) {
        Map<String, Object> header = Map.of(
                "alg", "HS512",
                "typ", "JWT"
        );

        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("access_key", accessKey);
        payload.put("nonce", UUID.randomUUID().toString());

        if (canonicalQuery != null && !canonicalQuery.isBlank()) {
            payload.put("query_hash", sha512Hex(canonicalQuery));
            payload.put("query_hash_alg", "SHA512");
        }

        String headerEncoded = base64UrlEncode(toJson(header));
        String payloadEncoded = base64UrlEncode(toJson(payload));
        String signingInput = headerEncoded + "." + payloadEncoded;

        return signingInput + "." + hmacSha512Base64Url(signingInput, secretKey);
    }

    private static String toJson(Map<String, Object> obj) {
        try {
            return OBJECT_MAPPER.writeValueAsString(obj);
        } catch (JsonProcessingException e) {
            throw new IllegalStateException("Failed to serialize JWT payload", e);
        }
    }

    private static String base64UrlEncode(String value) {
        return Base64.getUrlEncoder().withoutPadding().encodeToString(value.getBytes(StandardCharsets.UTF_8));
    }

    private static String sha512Hex(String value) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-512");
            byte[] hashed = digest.digest(value.getBytes(StandardCharsets.UTF_8));
            StringBuilder sb = new StringBuilder(hashed.length * 2);
            for (byte b : hashed) {
                sb.append(String.format("%02x", b));
            }
            return sb.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException("SHA-512 not available", e);
        }
    }

    private static String hmacSha512Base64Url(String value, String secret) {
        try {
            Mac mac = Mac.getInstance("HmacSHA512");
            mac.init(new SecretKeySpec(secret.getBytes(StandardCharsets.UTF_8), "HmacSHA512"));
            byte[] sig = mac.doFinal(value.getBytes(StandardCharsets.UTF_8));
            return Base64.getUrlEncoder().withoutPadding().encodeToString(sig);
        } catch (Exception e) {
            throw new IllegalStateException("Failed to sign JWT", e);
        }
    }
}
