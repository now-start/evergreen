package org.nowstart.evergreen.backtest.service;

import org.nowstart.evergreen.backtest.model.BacktestResult;
import org.nowstart.evergreen.backtest.model.BacktestRow;
import org.nowstart.evergreen.backtest.model.StrategyParams;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import javax.imageio.ImageIO;
import java.awt.BasicStroke;
import java.awt.Color;
import java.awt.Font;
import java.awt.Graphics2D;
import java.awt.RenderingHints;
import java.awt.Stroke;
import java.awt.image.BufferedImage;
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.ZoneId;
import java.time.ZoneOffset;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.LinkedHashSet;
import java.util.List;
import java.util.Locale;
import java.util.Set;

@Service
public class PlotService {

    private static final Logger log = LoggerFactory.getLogger(PlotService.class);
    private static final int WIDTH = 1280;
    private static final int HEIGHT = 720;
    private static final int LEFT_PADDING = 90;
    private static final int RIGHT_PADDING = 30;
    private static final int TOP_PADDING = 56;
    private static final int BOTTOM_PADDING = 80;
    private static final int CHART_WIDTH = WIDTH - LEFT_PADDING - RIGHT_PADDING;
    private static final int CHART_HEIGHT = HEIGHT - TOP_PADDING - BOTTOM_PADDING;
    private static final int Y_TICK_COUNT = 6;
    private static final int EXEC_MARKER_SIZE = 7;
    private static final int EXEC_MARKER_OFFSET = 14;
    private static final DateTimeFormatter FILE_STAMP_FORMAT = DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss");
    private static final DateTimeFormatter X_AXIS_DATE_FORMAT = DateTimeFormatter.ofPattern("yyyy-MM");
    private static final ZoneId X_AXIS_ZONE = ZoneId.of("Asia/Seoul");

    public void saveCharts(BacktestResult result, boolean drawGridLines, StrategyParams params) {
        String stamp = FILE_STAMP_FORMAT.format(java.time.LocalDateTime.now());
        Path outDir = Path.of("outputs", "charts");
        String paramTitle = formatParamTitle(params);
        try {
            Files.createDirectories(outDir);
            Path pricePath = outDir.resolve("price_signals_" + stamp + ".png");
            Path equityPath = outDir.resolve("equity_" + stamp + ".png");
            Path combinedPath = outDir.resolve("combined_" + stamp + ".png");
            savePriceChart(pricePath, result.rows(), drawGridLines, paramTitle);
            saveEquityChart(equityPath, result.rows(), drawGridLines, paramTitle);
            saveCombinedChart(combinedPath, pricePath, equityPath);
            log.info("[Backtest][PLOT] saved price={} equity={} combined={}",
                    pricePath.toAbsolutePath(), equityPath.toAbsolutePath(), combinedPath.toAbsolutePath());
        } catch (IOException e) {
            throw new IllegalStateException("Failed to save charts", e);
        }
    }

    private void savePriceChart(Path path, List<BacktestRow> rows, boolean grid, String paramTitle) throws IOException {
        requireRows(rows);

        BufferedImage image = new BufferedImage(WIDTH, HEIGHT, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = image.createGraphics();
        initCanvas(g, image.getWidth(), image.getHeight());

        double minClose = rows.stream().mapToDouble(BacktestRow::close).min().orElse(0.0);
        double maxClose = rows.stream().mapToDouble(BacktestRow::close).max().orElse(1.0);
        double min = minClose;
        double max = maxClose;
        min = Math.min(min, finiteMin(rows, i -> rows.get(i).ma(), minClose));
        min = Math.min(min, finiteMin(rows, i -> rows.get(i).regimeAnchor(), minClose));
        min = Math.min(min, finiteMin(rows, i -> rows.get(i).regimeUpper(), minClose));
        min = Math.min(min, finiteMin(rows, i -> rows.get(i).regimeLower(), minClose));
        min = Math.min(min, finiteMin(rows, i -> rows.get(i).atrTrailStop(), minClose));
        max = Math.max(max, finiteMax(rows, i -> rows.get(i).ma(), maxClose));
        max = Math.max(max, finiteMax(rows, i -> rows.get(i).regimeAnchor(), maxClose));
        max = Math.max(max, finiteMax(rows, i -> rows.get(i).regimeUpper(), maxClose));
        max = Math.max(max, finiteMax(rows, i -> rows.get(i).regimeLower(), maxClose));
        max = Math.max(max, finiteMax(rows, i -> rows.get(i).atrTrailStop(), maxClose));

        drawAxes(g, rows, min, max, grid, "Price (KRW)");

        drawDashedSeries(
                g,
                rows.size(),
                i -> rows.get(i).regimeUpper(),
                min,
                max,
                new Color(176, 176, 176),
                new float[]{5f, 4f}
        );
        drawDashedSeries(
                g,
                rows.size(),
                i -> rows.get(i).regimeLower(),
                min,
                max,
                new Color(176, 176, 176),
                new float[]{5f, 4f}
        );
        drawDashedSeries(
                g,
                rows.size(),
                i -> rows.get(i).regimeAnchor(),
                min,
                max,
                new Color(120, 120, 120),
                new float[]{8f, 4f}
        );

        drawSeries(g, rows.size(), i -> rows.get(i).close(), min, max, new Color(33, 150, 243));
        drawSeries(g, rows.size(), i -> rows.get(i).atrTrailStop(), min, max, new Color(192, 57, 43));

        for (int i = 0; i < rows.size(); i++) {
            int x = mapX(i, rows.size());
            int closeY = mapY(rows.get(i).close(), min, max);
            if (rows.get(i).setupBuy()) {
                drawCircleMarker(g, x, closeY - 8, 3, new Color(46, 204, 113, 200), new Color(26, 140, 79));
            }
            if (rows.get(i).setupSell()) {
                drawCircleMarker(g, x, closeY + 8, 3, new Color(243, 156, 18, 200), new Color(183, 120, 10));
            }
            if (rows.get(i).trailStopTriggered()) {
                drawCrossMarker(g, x, closeY, 4, new Color(192, 57, 43));
            }
            if (i > 0 && !rows.get(i).regime().equals(rows.get(i - 1).regime())) {
                double markerBase = Double.isFinite(rows.get(i).regimeAnchor()) ? rows.get(i).regimeAnchor() : rows.get(i).close();
                int regimeY = mapY(markerBase, min, max);
                drawSquareMarker(g, x, regimeY, 4, regimeColor(rows.get(i).regime()));
            }
        }

        g.setColor(new Color(46, 204, 113));
        for (int i = 1; i < rows.size(); i++) {
            // Mark execution points at open when exposure increases.
            if (rows.get(i).posOpen() > rows.get(i - 1).posOpen()) {
                int x = mapX(i, rows.size());
                int y = mapY(rows.get(i).open(), min, max);
                drawExecutionMarker(g, x, y, true, new Color(46, 204, 113));
            }
        }

        g.setColor(new Color(231, 76, 60));
        for (int i = 1; i < rows.size(); i++) {
            // Mark execution points at open when exposure decreases.
            if (rows.get(i).posOpen() < rows.get(i - 1).posOpen()) {
                int x = mapX(i, rows.size());
                int y = mapY(rows.get(i).open(), min, max);
                drawExecutionMarker(g, x, y, false, new Color(231, 76, 60));
            }
        }

        drawLegend(g, List.of(
                new LegendEntry("Close", new Color(33, 150, 243)),
                new LegendEntry("Regime EMA", new Color(120, 120, 120)),
                new LegendEntry("Regime Upper/Lower", new Color(176, 176, 176)),
                new LegendEntry("ATR Trail Stop", new Color(192, 57, 43)),
                new LegendEntry("Setup Buy Cond", new Color(46, 204, 113)),
                new LegendEntry("Setup Sell Cond", new Color(243, 156, 18)),
                new LegendEntry("Trail Stop Hit", new Color(192, 57, 43)),
                new LegendEntry("Regime Change", new Color(127, 140, 141)),
                new LegendEntry("Buy", new Color(46, 204, 113)),
                new LegendEntry("Sell", new Color(231, 76, 60))
        ));
        drawTitle(g, "Price + Signal Chart", paramTitle);

        g.dispose();
        ImageIO.write(image, "png", path.toFile());
    }

    private void saveEquityChart(Path path, List<BacktestRow> rows, boolean grid, String paramTitle) throws IOException {
        requireRows(rows);

        BufferedImage image = new BufferedImage(WIDTH, HEIGHT, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = image.createGraphics();
        initCanvas(g, image.getWidth(), image.getHeight());

        double minStrategy = rows.stream().mapToDouble(BacktestRow::equity).min().orElse(1.0);
        double minBh = rows.stream().mapToDouble(BacktestRow::equityBh).min().orElse(1.0);
        double maxStrategy = rows.stream().mapToDouble(BacktestRow::equity).max().orElse(1.0);
        double maxBh = rows.stream().mapToDouble(BacktestRow::equityBh).max().orElse(1.0);
        double min = Math.min(minStrategy, minBh);
        double max = Math.max(maxStrategy, maxBh);

        drawAxes(g, rows, min, max, grid, "Equity");
        drawSeries(g, rows.size(), i -> rows.get(i).equity(), min, max, new Color(52, 73, 94));
        drawSeries(g, rows.size(), i -> rows.get(i).equityBh(), min, max, new Color(155, 89, 182));

        for (int i = 1; i < rows.size(); i++) {
            if (rows.get(i).posOpen() > rows.get(i - 1).posOpen()) {
                int x = mapX(i, rows.size());
                int y = mapY(rows.get(i).equity(), min, max);
                drawExecutionMarker(g, x, y, true, new Color(46, 204, 113));
            }
            if (rows.get(i).posOpen() < rows.get(i - 1).posOpen()) {
                int x = mapX(i, rows.size());
                int y = mapY(rows.get(i).equity(), min, max);
                drawExecutionMarker(g, x, y, false, new Color(231, 76, 60));
            }
        }

        drawLegend(g, List.of(
                new LegendEntry("Strategy", new Color(52, 73, 94)),
                new LegendEntry("Buy&Hold", new Color(155, 89, 182)),
                new LegendEntry("Buy", new Color(46, 204, 113)),
                new LegendEntry("Sell", new Color(231, 76, 60))
        ));
        drawTitle(g, "Equity Curve", paramTitle);

        g.dispose();
        ImageIO.write(image, "png", path.toFile());
    }

    private void initCanvas(Graphics2D g, int width, int height) {
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
        g.setColor(Color.WHITE);
        g.fillRect(0, 0, width, height);
    }

    private void drawAxes(Graphics2D g, List<BacktestRow> rows, double min, double max, boolean grid, String yAxisName) {
        double span = Math.max(1e-12, max - min);

        for (int i = 0; i < Y_TICK_COUNT; i++) {
            double ratio = i / (double) (Y_TICK_COUNT - 1);
            double value = max - (span * ratio);
            int y = TOP_PADDING + (int) Math.round(CHART_HEIGHT * ratio);

            if (grid) {
                g.setColor(new Color(232, 232, 232));
                g.drawLine(LEFT_PADDING, y, LEFT_PADDING + CHART_WIDTH, y);
            }

            g.setColor(Color.GRAY);
            g.drawLine(LEFT_PADDING - 5, y, LEFT_PADDING, y);
            g.drawString(formatValue(value, span), 8, y + 5);
        }

        for (int index : xTickIndices(rows.size())) {
            int x = mapX(index, rows.size());
            if (grid) {
                g.setColor(new Color(232, 232, 232));
                g.drawLine(x, TOP_PADDING, x, TOP_PADDING + CHART_HEIGHT);
            }

            String label = X_AXIS_DATE_FORMAT.format(rows.get(index).timestamp().atZone(X_AXIS_ZONE));
            g.setColor(Color.GRAY);
            g.drawLine(x, TOP_PADDING + CHART_HEIGHT, x, TOP_PADDING + CHART_HEIGHT + 5);
            g.drawString(label, x - 20, TOP_PADDING + CHART_HEIGHT + 24);
        }

        g.setColor(Color.BLACK);
        g.drawRect(LEFT_PADDING, TOP_PADDING, CHART_WIDTH, CHART_HEIGHT);
        g.drawString(yAxisName, LEFT_PADDING - 70, TOP_PADDING - 10);
        g.drawString("Date (KST)", LEFT_PADDING + CHART_WIDTH - 100, TOP_PADDING + CHART_HEIGHT + 48);
    }

    private List<Integer> xTickIndices(int n) {
        if (n <= 1) {
            return List.of(0);
        }
        int tickCount = 6;
        Set<Integer> indexSet = new LinkedHashSet<>();
        for (int i = 0; i < tickCount; i++) {
            int index = (int) Math.round(((n - 1) * i) / (double) (tickCount - 1));
            indexSet.add(index);
        }
        return new ArrayList<>(indexSet);
    }

    private String formatValue(double value, double span) {
        if (span < 10.0) {
            return String.format(Locale.US, "%.4f", value);
        }
        if (span < 1000.0) {
            return String.format(Locale.US, "%.2f", value);
        }
        return String.format(Locale.US, "%,.0f", value);
    }

    private void drawLegend(Graphics2D g, List<LegendEntry> entries) {
        int boxWidth = 220;
        int lineHeight = 16;
        int x = LEFT_PADDING + CHART_WIDTH - boxWidth - 12;
        int y = TOP_PADDING + 12;

        g.setColor(new Color(255, 255, 255, 220));
        g.fillRect(x - 8, y - 14, boxWidth, (entries.size() * lineHeight) + 10);
        g.setColor(Color.GRAY);
        g.drawRect(x - 8, y - 14, boxWidth, (entries.size() * lineHeight) + 10);

        for (int i = 0; i < entries.size(); i++) {
            LegendEntry entry = entries.get(i);
            int yPos = y + (i * lineHeight);
            g.setColor(entry.color());
            g.drawLine(x, yPos, x + 20, yPos);
            g.drawLine(x, yPos + 1, x + 20, yPos + 1);
            g.setColor(Color.BLACK);
            g.drawString(entry.label(), x + 28, yPos + 4);
        }
    }

    private void drawTitle(Graphics2D g, String title, String subtitle) {
        g.setColor(Color.BLACK);
        Font original = g.getFont();
        g.drawString(title, LEFT_PADDING, 24);
        g.setFont(original.deriveFont(11f));
        g.setColor(new Color(80, 80, 80));
        g.drawString(subtitle, LEFT_PADDING, 42);
        g.setFont(original);
    }

    private void saveCombinedChart(Path outPath, Path topChartPath, Path bottomChartPath) throws IOException {
        BufferedImage top = ImageIO.read(topChartPath.toFile());
        BufferedImage bottom = ImageIO.read(bottomChartPath.toFile());
        if (top == null || bottom == null) {
            throw new IOException("Failed to read chart images for combined output");
        }

        int width = Math.max(top.getWidth(), bottom.getWidth());
        int height = top.getHeight() + bottom.getHeight();
        BufferedImage merged = new BufferedImage(width, height, BufferedImage.TYPE_INT_ARGB);
        Graphics2D g = merged.createGraphics();
        g.setRenderingHint(RenderingHints.KEY_ANTIALIASING, RenderingHints.VALUE_ANTIALIAS_ON);
        g.setColor(Color.WHITE);
        g.fillRect(0, 0, width, height);
        g.drawImage(top, 0, 0, null);
        g.drawImage(bottom, 0, top.getHeight(), null);
        g.setColor(new Color(220, 220, 220));
        g.drawLine(0, top.getHeight(), width, top.getHeight());
        g.dispose();

        ImageIO.write(merged, "png", outPath.toFile());
    }

    private String formatParamTitle(StrategyParams params) {
        return String.format(
                Locale.US,
                "RegimeFlip(BEAR->BULL / BULL->BEAR), EMA=%d, ATR(%d)x%.1f, regimeBand=%.2f",
                params.regimeEmaLen(),
                params.atrPeriod(),
                params.atrTrailMultiplier(),
                params.regimeBand()
        );
    }

    private void drawExecutionMarker(Graphics2D g, int x, int priceY, boolean isBuy, Color fillColor) {
        int markerCenterY = isBuy ? priceY - EXEC_MARKER_OFFSET : priceY + EXEC_MARKER_OFFSET;
        int tipY = isBuy ? markerCenterY - EXEC_MARKER_SIZE : markerCenterY + EXEC_MARKER_SIZE;
        int baseY = isBuy ? markerCenterY + EXEC_MARKER_SIZE : markerCenterY - EXEC_MARKER_SIZE;

        g.setColor(new Color(70, 70, 70, 150));
        g.drawLine(x, priceY, x, tipY);

        int outlineSize = EXEC_MARKER_SIZE + 2;
        int outlineTipY = isBuy ? markerCenterY - outlineSize : markerCenterY + outlineSize;
        int outlineBaseY = isBuy ? markerCenterY + outlineSize : markerCenterY - outlineSize;

        int[] ox = {x, x - outlineSize, x + outlineSize};
        int[] oy = {outlineTipY, outlineBaseY, outlineBaseY};
        g.setColor(Color.WHITE);
        g.fillPolygon(ox, oy, 3);

        int[] px = {x, x - EXEC_MARKER_SIZE, x + EXEC_MARKER_SIZE};
        int[] py = {tipY, baseY, baseY};
        g.setColor(fillColor);
        g.fillPolygon(px, py, 3);

        g.setColor(Color.DARK_GRAY);
        g.drawPolygon(px, py, 3);
        g.drawString(isBuy ? "B" : "S", x + EXEC_MARKER_SIZE + 3, markerCenterY + 4);
    }

    private void requireRows(List<BacktestRow> rows) {
        if (rows == null || rows.isEmpty()) {
            throw new IllegalArgumentException("Backtest rows are empty");
        }
    }

    private double finiteMin(List<BacktestRow> rows, ValueAt valueAt, double fallback) {
        double min = Double.POSITIVE_INFINITY;
        for (int i = 0; i < rows.size(); i++) {
            double value = valueAt.get(i);
            if (Double.isFinite(value)) {
                min = Math.min(min, value);
            }
        }
        return min == Double.POSITIVE_INFINITY ? fallback : min;
    }

    private double finiteMax(List<BacktestRow> rows, ValueAt valueAt, double fallback) {
        double max = Double.NEGATIVE_INFINITY;
        for (int i = 0; i < rows.size(); i++) {
            double value = valueAt.get(i);
            if (Double.isFinite(value)) {
                max = Math.max(max, value);
            }
        }
        return max == Double.NEGATIVE_INFINITY ? fallback : max;
    }

    private void drawSeries(Graphics2D g, int n, ValueAt indexValue, double min, double max, Color color) {
        g.setColor(color);
        int prevX = -1;
        int prevY = -1;
        for (int i = 0; i < n; i++) {
            double v = indexValue.get(i);
            if (Double.isNaN(v)) {
                continue;
            }
            int x = mapX(i, n);
            int y = mapY(v, min, max);
            if (prevX >= 0) {
                g.drawLine(prevX, prevY, x, y);
            }
            prevX = x;
            prevY = y;
        }
    }

    private void drawDashedSeries(
            Graphics2D g,
            int n,
            ValueAt indexValue,
            double min,
            double max,
            Color color,
            float[] dashPattern
    ) {
        Stroke original = g.getStroke();
        g.setStroke(new BasicStroke(1.2f, BasicStroke.CAP_BUTT, BasicStroke.JOIN_ROUND, 10.0f, dashPattern, 0f));
        drawSeries(g, n, indexValue, min, max, color);
        g.setStroke(original);
    }

    private void drawCircleMarker(Graphics2D g, int x, int y, int radius, Color fill, Color border) {
        int d = radius * 2;
        g.setColor(fill);
        g.fillOval(x - radius, y - radius, d, d);
        g.setColor(border);
        g.drawOval(x - radius, y - radius, d, d);
    }

    private void drawSquareMarker(Graphics2D g, int x, int y, int halfSize, Color fill) {
        int size = halfSize * 2;
        g.setColor(fill);
        g.fillRect(x - halfSize, y - halfSize, size, size);
        g.setColor(Color.DARK_GRAY);
        g.drawRect(x - halfSize, y - halfSize, size, size);
    }

    private void drawCrossMarker(Graphics2D g, int x, int y, int halfSize, Color color) {
        g.setColor(color);
        g.drawLine(x - halfSize, y - halfSize, x + halfSize, y + halfSize);
        g.drawLine(x - halfSize, y + halfSize, x + halfSize, y - halfSize);
    }

    private Color regimeColor(String regime) {
        return switch (regime) {
            case "BULL" -> new Color(39, 174, 96);
            case "BEAR" -> new Color(192, 57, 43);
            default -> new Color(241, 196, 15);
        };
    }

    private int mapX(int i, int n) {
        if (n <= 1) {
            return LEFT_PADDING;
        }
        return LEFT_PADDING + (int) ((CHART_WIDTH * (double) i) / (n - 1));
    }

    private int mapY(double value, double min, double max) {
        if (max <= min) {
            return TOP_PADDING + (CHART_HEIGHT / 2);
        }
        double ratio = (value - min) / (max - min);
        return TOP_PADDING + CHART_HEIGHT - (int) (CHART_HEIGHT * ratio);
    }

    @FunctionalInterface
    private interface ValueAt {
        double get(int idx);
    }

    private record LegendEntry(String label, Color color) {}
}
