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
        Integer gridParallelism,
        Integer gridProgressLogSeconds,
        String gridRsiRange,
        String gridRangeRsiRange,
        String gridBearRsiRange,
        String gridMaLenRange,
        String gridMaSlopeRange,
        String gridAtrPeriodRange,
        String gridAtrTrailMultRange,
        String gridRegimeBandRange,
        Boolean plotGridLines
) {
    private static final Instant DEFAULT_FROM = Instant.parse("2020-01-01T00:00:00Z");
    private static final String DEFAULT_CSV_CACHE_DIR = "outputs/data/upbit-cache";
    private static final double DEFAULT_VALIDATION_RATIO = 0.7;
    private static final int DEFAULT_GRID_PARALLELISM = Math.max(1, Runtime.getRuntime().availableProcessors());
    private static final int DEFAULT_GRID_PROGRESS_LOG_SECONDS = 5;
    private static final String DEFAULT_BULL_RSI_RANGE = "10:60:10";
    private static final String DEFAULT_RANGE_RSI_RANGE = "10:40:10";
    private static final String DEFAULT_BEAR_RSI_RANGE = "10:30:10";
    private static final String DEFAULT_MA_LEN_RANGE = "200:200:1";
    private static final String DEFAULT_MA_SLOPE_RANGE = "1:19:3";
    private static final String DEFAULT_ATR_PERIOD_RANGE = "14:14:1";
    private static final String DEFAULT_ATR_TRAIL_MULT_RANGE = "3:3:1";
    private static final String DEFAULT_REGIME_BAND_RANGE = "0.02:0.02:0.01";

    public BacktestConfig {
        enabled = enabled != null ? enabled : true;
        market = market != null ? market.trim() : "";
        fromDt = fromDt != null ? fromDt : DEFAULT_FROM;
        toDt = toDt != null ? toDt : Instant.now();
        csvCacheDir = normalizeSpec(csvCacheDir, DEFAULT_CSV_CACHE_DIR);
        validationRatio = validationRatio != null ? validationRatio : DEFAULT_VALIDATION_RATIO;
        feePerSide = feePerSide != null ? feePerSide : 0.0005;
        slippage = slippage != null ? slippage : 0.0002;
        topK = topK != null ? topK : 10;
        gridParallelism = gridParallelism != null ? gridParallelism : DEFAULT_GRID_PARALLELISM;
        gridProgressLogSeconds = gridProgressLogSeconds != null ? gridProgressLogSeconds : DEFAULT_GRID_PROGRESS_LOG_SECONDS;
        gridRsiRange = normalizeSpec(gridRsiRange, DEFAULT_BULL_RSI_RANGE);
        gridRangeRsiRange = normalizeSpec(gridRangeRsiRange, DEFAULT_RANGE_RSI_RANGE);
        gridBearRsiRange = normalizeSpec(gridBearRsiRange, DEFAULT_BEAR_RSI_RANGE);
        gridMaLenRange = normalizeSpec(gridMaLenRange, DEFAULT_MA_LEN_RANGE);
        gridMaSlopeRange = normalizeSpec(gridMaSlopeRange, DEFAULT_MA_SLOPE_RANGE);
        gridAtrPeriodRange = normalizeSpec(gridAtrPeriodRange, DEFAULT_ATR_PERIOD_RANGE);
        gridAtrTrailMultRange = normalizeSpec(gridAtrTrailMultRange, DEFAULT_ATR_TRAIL_MULT_RANGE);
        gridRegimeBandRange = normalizeSpec(gridRegimeBandRange, DEFAULT_REGIME_BAND_RANGE);
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
        if (gridParallelism <= 0) {
            throw new IllegalArgumentException("grid-parallelism must be > 0");
        }
        if (gridProgressLogSeconds <= 0) {
            throw new IllegalArgumentException("grid-progress-log-seconds must be > 0");
        }
    }

    public List<Double> resolveBullRsiValues() {
        return parseRsiRange(gridRsiRange, "grid-rsi-range");
    }

    public List<Double> resolveRangeRsiValues() {
        return parseRsiRange(gridRangeRsiRange, "grid-range-rsi-range");
    }

    public List<Double> resolveBearRsiValues() {
        return parseRsiRange(gridBearRsiRange, "grid-bear-rsi-range");
    }

    public List<Integer> resolveMaLenValues() {
        return parsePositiveIntRange(gridMaLenRange, "grid-ma-len-range");
    }

    public List<Integer> resolveMaSlopeValues() {
        return parseNonNegativeIntRange(gridMaSlopeRange, "grid-ma-slope-range");
    }

    public List<Integer> resolveAtrPeriodValues() {
        return parsePositiveIntRange(gridAtrPeriodRange, "grid-atr-period-range");
    }

    public List<Double> resolveAtrTrailMultipliers() {
        List<Double> values = parseDoubleRange(gridAtrTrailMultRange);
        for (double value : values) {
            if (value < 0.0) {
                throw new IllegalArgumentException("grid-atr-trail-mult-range must be >= 0");
            }
        }
        return values;
    }

    public List<Double> resolveRegimeBandValues() {
        List<Double> values = parseDoubleRange(gridRegimeBandRange);
        for (double value : values) {
            if (value < 0.0 || value >= 1.0) {
                throw new IllegalArgumentException("grid-regime-band-range must be in [0, 1)");
            }
        }
        return values;
    }

    public long combinationCount() {
        long total = 1L;
        total = multiply(total, resolveMaLenValues().size());
        total = multiply(total, resolveAtrPeriodValues().size());
        total = multiply(total, resolveAtrTrailMultipliers().size());
        total = multiply(total, resolveRegimeBandValues().size());
        return total;
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

    private static long multiply(long left, int right) {
        if (right <= 0) {
            throw new IllegalArgumentException("grid axis must not be empty");
        }
        if (left > Long.MAX_VALUE / right) {
            throw new IllegalArgumentException("grid combination count overflow");
        }
        return left * right;
    }

    private static String normalizeSpec(String raw, String defaults) {
        if (raw == null || raw.isBlank()) {
            return defaults;
        }
        return raw.trim();
    }

    private static List<Double> parseRsiRange(String spec, String fieldName) {
        List<Double> values = parseDoubleRange(spec);
        for (double value : values) {
            if (value <= 0.0 || value >= 100.0) {
                throw new IllegalArgumentException(fieldName + " values must be in (0, 100)");
            }
        }
        return values;
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
        for (double value = start; value <= end + 1e-12; value += step) {
            out.add(value);
        }
        return List.copyOf(out);
    }

    private static List<Integer> parsePositiveIntRange(String spec, String fieldName) {
        List<Integer> out = parseIntRange(spec);
        for (int value : out) {
            if (value <= 0) {
                throw new IllegalArgumentException(fieldName + " values must be > 0");
            }
        }
        return out;
    }

    private static List<Integer> parseNonNegativeIntRange(String spec, String fieldName) {
        List<Integer> out = parseIntRange(spec);
        for (int value : out) {
            if (value < 0) {
                throw new IllegalArgumentException(fieldName + " values must be >= 0");
            }
        }
        return out;
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
