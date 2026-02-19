package org.nowstart.evergreen.backtest.config;

import org.springframework.boot.context.properties.ConfigurationProperties;

import java.time.Instant;
import java.util.ArrayList;
import java.util.List;

@SuppressWarnings("ConfigurationProperties")
@ConfigurationProperties(prefix = "backtest")
public record BacktestConfig(
        Boolean enabled,
        String market,
        Instant fromDt,
        Instant toDt,
        String csvCacheDir,
        Double validationRatio,
        Double feePerSide,
        Double slippage,
        Integer topK,
        String gridRsiRange,
        String gridMaLenRange,
        String gridMaSlopeRange,
        Boolean plotGridLines
) {
    private static final Instant DEFAULT_FROM = Instant.parse("2020-01-01T00:00:00Z");
    private static final String DEFAULT_CSV_CACHE_DIR = "outputs/data/upbit-cache";
    private static final double DEFAULT_VALIDATION_RATIO = 0.7;
    private static final String DEFAULT_RSI_RANGE = "20:45:5";
    private static final String DEFAULT_MA_LEN_RANGE = "20:120:10";
    private static final String DEFAULT_MA_SLOPE_RANGE = "1:9:2";

    public BacktestConfig {
        enabled = enabled != null ? enabled : true;
        market = market != null ? market.trim() : "";
        fromDt = fromDt != null ? fromDt : DEFAULT_FROM;
        toDt = toDt != null ? toDt : Instant.now();
        csvCacheDir = normalizeSpec(csvCacheDir, DEFAULT_CSV_CACHE_DIR);
        validationRatio = validationRatio != null ? validationRatio : DEFAULT_VALIDATION_RATIO;
        feePerSide = feePerSide != null ? feePerSide : 0.0005;
        slippage = slippage != null ? slippage : 0.0002;
        topK = topK != null ? topK : 1;
        gridRsiRange = normalizeSpec(gridRsiRange, DEFAULT_RSI_RANGE);
        gridMaLenRange = normalizeSpec(gridMaLenRange, DEFAULT_MA_LEN_RANGE);
        gridMaSlopeRange = normalizeSpec(gridMaSlopeRange, DEFAULT_MA_SLOPE_RANGE);
        plotGridLines = plotGridLines != null ? plotGridLines : true;

        if (market.isBlank()) {
            throw new IllegalArgumentException("market must not be blank");
        }
        if (csvCacheDir.isBlank()) {
            throw new IllegalArgumentException("csv-cache-dir must not be blank");
        }
        if (validationRatio <= 0.0 || validationRatio >= 1.0) {
            throw new IllegalArgumentException("validation-ratio must be between 0 and 1");
        }
        if (fromDt.isAfter(toDt)) {
            throw new IllegalArgumentException("from-dt must be <= to-dt");
        }
        if (feePerSide < 0 || slippage < 0) {
            throw new IllegalArgumentException("fee-per-side and slippage must be >= 0");
        }
        if (topK <= 0) {
            throw new IllegalArgumentException("top-k must be > 0");
        }
    }

    public List<Double> resolveRsiValues() {
        return parseDoubleRange(gridRsiRange);
    }

    public List<Integer> resolveMaLenValues() {
        return parseIntRange(gridMaLenRange);
    }

    public List<Integer> resolveMaSlopeValues() {
        return parseIntRange(gridMaSlopeRange);
    }

    public int combinationCount() {
        return resolveRsiValues().size() * resolveMaLenValues().size() * resolveMaSlopeValues().size();
    }

    public int validationSplitIndex(int totalBars) {
        if (totalBars < 4) {
            throw new IllegalArgumentException("At least 4 bars are required for validation/test split");
        }
        int split = (int) Math.floor(totalBars * validationRatio);
        if (split < 2) {
            split = 2;
        }
        if (split > totalBars - 2) {
            split = totalBars - 2;
        }
        return split;
    }

    private static String normalizeSpec(String raw, String defaults) {
        if (raw == null || raw.isBlank()) {
            return defaults;
        }
        return raw.trim();
    }

    private static List<Double> parseDoubleRange(String spec) {
        String[] parts = spec.split(":");
        if (parts.length != 3) {
            throw new IllegalArgumentException("range must be start:end:step, got: " + spec);
        }
        double start = Double.parseDouble(parts[0].trim());
        double end = Double.parseDouble(parts[1].trim());
        double step = Double.parseDouble(parts[2].trim());
        if (step <= 0) {
            throw new IllegalArgumentException("range step must be > 0, got: " + spec);
        }
        if (end < start) {
            throw new IllegalArgumentException("range end must be >= start, got: " + spec);
        }

        List<Double> out = new ArrayList<>();
        for (double v = start; v <= end + 1e-12; v += step) {
            out.add(v);
        }
        return List.copyOf(out);
    }

    private static List<Integer> parseIntRange(String spec) {
        List<Double> doubles = parseDoubleRange(spec);
        List<Integer> out = new ArrayList<>(doubles.size());
        for (Double value : doubles) {
            out.add((int) Math.round(value));
        }
        return List.copyOf(out);
    }
}
