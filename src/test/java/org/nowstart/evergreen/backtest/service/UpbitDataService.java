package org.nowstart.evergreen.backtest.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import org.nowstart.evergreen.backtest.config.BacktestConfig;
import org.nowstart.evergreen.backtest.model.CandleBar;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.time.Instant;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.Comparator;
import java.util.HashMap;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

@Service
public class UpbitDataService {

    private static final String UPBIT_DAYS_URL = "https://api.upbit.com/v1/candles/days";
    private static final String CSV_HEADER = "candle_date_time_utc,opening_price,high_price,low_price,trade_price,candle_acc_trade_volume";
    private static final DateTimeFormatter DATE_KEY_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd");
    private static final Logger log = LoggerFactory.getLogger(UpbitDataService.class);
    private static final int MAX_RETRIES = 6;
    private static final long BASE_BACKOFF_MS = 400L;
    private static final long MIN_REQUEST_INTERVAL_MS = 150L;

    private final ObjectMapper objectMapper = new ObjectMapper();
    private final HttpClient httpClient = HttpClient.newHttpClient();
    private long lastRequestAtMs = 0L;

    public List<CandleBar> loadBars(BacktestConfig config) {
        Path cachePath = resolveCachePath(config.market(), config.fromDt(), config.toDt(), config.csvCacheDir());

        if (Files.exists(cachePath)) {
            try {
                List<CandleBar> cached = loadCsv(cachePath);
                log.info("[Backtest][DATA] cache hit path={} rows={}", cachePath.toAbsolutePath(), cached.size());
                return cached;
            } catch (RuntimeException e) {
                log.warn("[Backtest][DATA] cache read failed path={}, fallback to api", cachePath.toAbsolutePath(), e);
            }
        }

        log.info("[Backtest][DATA] cache miss path={}, fetch from api", cachePath.toAbsolutePath());
        List<CandleBar> bars = fetchFromApi(config.market(), config.fromDt(), config.toDt());
        saveCsv(cachePath, bars);
        log.info("[Backtest][DATA] cache saved path={} rows={}", cachePath.toAbsolutePath(), bars.size());
        return bars;
    }

    public List<CandleBar> fetchFromApi(String market, Instant from, Instant to) {
        Map<String, CandleBar> dedup = new HashMap<>();
        Instant cursor = to;

        while (true) {
            List<CandleBar> batch = requestBatch(market, cursor, 200);
            if (batch.isEmpty()) {
                break;
            }

            Instant oldest = batch.get(batch.size() - 1).timestamp();
            for (CandleBar bar : batch) {
                if (bar.timestamp().isBefore(from) || bar.timestamp().isAfter(to)) {
                    continue;
                }
                dedup.put(bar.timestamp().toString(), bar);
            }

            if (!oldest.isAfter(from)) {
                break;
            }
            cursor = oldest.minusSeconds(1);
        }

        List<CandleBar> out = new ArrayList<>(dedup.values());
        out.sort(Comparator.comparing(CandleBar::timestamp));
        return out;
    }

    private List<CandleBar> requestBatch(String market, Instant to, int count) {
        String url = UPBIT_DAYS_URL
                + "?market=" + URLEncoder.encode(market, StandardCharsets.UTF_8)
                + "&to=" + URLEncoder.encode(to.toString(), StandardCharsets.UTF_8)
                + "&count=" + count;

        for (int attempt = 1; attempt <= MAX_RETRIES; attempt++) {
            try {
                waitForRateLimitWindow();

                HttpRequest req = HttpRequest.newBuilder(URI.create(url))
                        .timeout(Duration.ofSeconds(15))
                        .header("Accept", "application/json")
                        .header("User-Agent", "evergreen-backtest/1.0")
                        .GET()
                        .build();

                HttpResponse<String> response = httpClient.send(req, HttpResponse.BodyHandlers.ofString());
                int status = response.statusCode();

                if (status >= 200 && status < 300) {
                    return parseBars(response.body());
                }

                boolean retryable = status == 429 || status >= 500;
                if (!retryable || attempt == MAX_RETRIES) {
                    throw new IllegalStateException("Upbit API error: " + status + " body=" + abbreviate(response.body()));
                }

                long waitMs = retryAfterMs(response, attempt);
                log.warn("Upbit API temporary error status={}, retry attempt={}/{}, waitMs={}", status, attempt, MAX_RETRIES, waitMs);
                sleep(waitMs);
            } catch (InterruptedException e) {
                Thread.currentThread().interrupt();
                throw new IllegalStateException("Failed to call Upbit API", e);
            } catch (IOException e) {
                if (attempt == MAX_RETRIES) {
                    throw new IllegalStateException("Failed to call Upbit API", e);
                }
                long waitMs = BASE_BACKOFF_MS * (1L << (attempt - 1));
                log.warn("Upbit API IO error, retry attempt={}/{}, waitMs={}", attempt, MAX_RETRIES, waitMs);
                sleep(waitMs);
            }
        }

        throw new IllegalStateException("Failed to call Upbit API after retries");
    }

    private List<CandleBar> parseBars(String body) throws IOException {
        JsonNode root = objectMapper.readTree(body);
        List<CandleBar> bars = new ArrayList<>();
        for (JsonNode row : root) {
            Instant ts = parseTs(row.path("candle_date_time_utc").asText());
            bars.add(new CandleBar(
                    ts,
                    row.path("opening_price").asDouble(),
                    row.path("high_price").asDouble(),
                    row.path("low_price").asDouble(),
                    row.path("trade_price").asDouble(),
                    row.path("candle_acc_trade_volume").asDouble()
            ));
        }
        bars.sort((a, b) -> b.timestamp().compareTo(a.timestamp()));
        return bars;
    }

    private synchronized void waitForRateLimitWindow() throws InterruptedException {
        long now = System.currentTimeMillis();
        long waitMs = MIN_REQUEST_INTERVAL_MS - (now - lastRequestAtMs);
        if (waitMs > 0) {
            Thread.sleep(waitMs);
        }
        lastRequestAtMs = System.currentTimeMillis();
    }

    private long retryAfterMs(HttpResponse<String> response, int attempt) {
        long retryAfterHeaderMs = response.headers()
                .firstValue("Retry-After")
                .map(this::parseRetryAfterMs)
                .orElse(0L);
        if (retryAfterHeaderMs > 0) {
            return retryAfterHeaderMs;
        }
        return BASE_BACKOFF_MS * (1L << (attempt - 1));
    }

    private long parseRetryAfterMs(String value) {
        try {
            long sec = Long.parseLong(value.trim());
            return Math.max(0L, sec * 1000L);
        } catch (NumberFormatException ignored) {
            return 0L;
        }
    }

    private void sleep(long waitMs) {
        try {
            Thread.sleep(waitMs);
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Interrupted while waiting for Upbit API retry", e);
        }
    }

    private String abbreviate(String body) {
        if (body == null) {
            return "";
        }
        String trimmed = body.replaceAll("\\s+", " ").trim();
        if (trimmed.length() <= 200) {
            return trimmed;
        }
        return trimmed.substring(0, 200);
    }

    private Instant parseTs(String raw) {
        String ts = raw.trim();
        if (ts.endsWith("Z") || ts.endsWith("z")) {
            return Instant.parse(ts);
        }
        return Instant.parse(ts + "Z");
    }

    private Path resolveCachePath(String market, Instant from, Instant to, String cacheDir) {
        String safeMarket = market.replaceAll("[^A-Za-z0-9._-]", "_");
        String fromKey = DATE_KEY_FORMAT.format(from.atOffset(ZoneOffset.UTC));
        String toKey = DATE_KEY_FORMAT.format(to.atOffset(ZoneOffset.UTC));
        String fileName = safeMarket + "_" + fromKey + "_" + toKey + "_days.csv";
        return Path.of(cacheDir, fileName);
    }

    private List<CandleBar> loadCsv(Path path) {
        try {
            List<String> lines = Files.readAllLines(path, StandardCharsets.UTF_8);
            if (lines.size() < 2) {
                throw new IllegalArgumentException("CSV has no rows: " + path);
            }

            Map<String, CandleBar> dedup = new LinkedHashMap<>();
            for (int i = 1; i < lines.size(); i++) {
                String line = lines.get(i).trim();
                if (line.isEmpty()) {
                    continue;
                }
                String[] parts = splitCsvLine(line);
                if (parts.length < 6) {
                    continue;
                }
                Instant ts = parseTs(parts[0]);
                CandleBar bar = new CandleBar(
                        ts,
                        parseDouble(parts[1]),
                        parseDouble(parts[2]),
                        parseDouble(parts[3]),
                        parseDouble(parts[4]),
                        parseDouble(parts[5])
                );
                dedup.put(ts.toString(), bar);
            }

            List<CandleBar> out = new ArrayList<>(dedup.values());
            out.sort(Comparator.comparing(CandleBar::timestamp));
            return out;
        } catch (IOException e) {
            throw new IllegalStateException("Failed to load CSV: " + path, e);
        }
    }

    private void saveCsv(Path path, List<CandleBar> bars) {
        try {
            if (path.getParent() != null) {
                Files.createDirectories(path.getParent());
            }

            List<String> lines = new ArrayList<>(bars.size() + 1);
            lines.add(CSV_HEADER);
            for (CandleBar b : bars) {
                lines.add(
                        b.timestamp().toString()
                                + "," + b.open()
                                + "," + b.high()
                                + "," + b.low()
                                + "," + b.close()
                                + "," + b.volume()
                );
            }

            Files.write(path, lines, StandardCharsets.UTF_8);
        } catch (IOException e) {
            throw new IllegalStateException("Failed to save CSV: " + path, e);
        }
    }

    private String[] splitCsvLine(String line) {
        return line.split(",", -1);
    }

    private double parseDouble(String raw) {
        return Double.parseDouble(raw.trim());
    }
}
