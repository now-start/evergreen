package org.nowstart.evergreen.service.auth;

import com.fasterxml.jackson.core.type.TypeReference;
import com.fasterxml.jackson.databind.ObjectMapper;
import feign.RequestInterceptor;
import feign.RequestTemplate;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import lombok.RequiredArgsConstructor;

@RequiredArgsConstructor
public class UpbitAuthRequestInterceptor implements RequestInterceptor {

    private static final ObjectMapper OBJECT_MAPPER = new ObjectMapper();

    private final UpbitJwtSigner upbitJwtSigner;

    @Override
    public void apply(RequestTemplate template) {
        String canonicalQuery = buildCanonicalQuery(template);
        String token = upbitJwtSigner.createToken(canonicalQuery);
        template.header("Authorization", "Bearer " + token);
        template.header("Accept", "application/json");
        template.header("User-Agent", "evergreen-backtest/1.0");
    }

    private String buildCanonicalQuery(RequestTemplate template) {
        byte[] body = template.body();
        if (body != null && body.length > 0) {
            try {
                Map<String, Object> map = OBJECT_MAPPER.readValue(body, new TypeReference<>() {});
                return toCanonicalQuery(map);
            } catch (IOException e) {
                return new String(body, StandardCharsets.UTF_8);
            }
        }

        if (template.queries() == null || template.queries().isEmpty()) {
            return "";
        }

        Map<String, Object> query = new LinkedHashMap<>();
        template.queries().forEach((key, values) -> {
            if (values == null || values.isEmpty()) {
                return;
            }
            if (values.size() == 1) {
                query.put(key, values.iterator().next());
                return;
            }
            query.put(key, new ArrayList<>(values));
        });

        return toCanonicalQuery(query);
    }

    private String toCanonicalQuery(Map<String, Object> payload) {
        List<Map.Entry<String, Object>> entries = new ArrayList<>(payload.entrySet());
        entries.sort(Comparator.comparing(Map.Entry::getKey));

        List<String> pairs = new ArrayList<>();
        for (Map.Entry<String, Object> entry : entries) {
            Object value = entry.getValue();
            if (value == null) {
                continue;
            }
            if (value instanceof List<?> list) {
                for (Object item : list) {
                    if (item != null) {
                        pairs.add(entry.getKey() + "=" + item);
                    }
                }
            } else {
                pairs.add(entry.getKey() + "=" + value);
            }
        }

        return String.join("&", pairs);
    }
}
