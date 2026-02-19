package org.nowstart.evergreen.backtest.service;

import org.nowstart.evergreen.backtest.config.BacktestConfig;
import org.nowstart.evergreen.backtest.model.BacktestResult;
import org.nowstart.evergreen.backtest.model.CandleBar;
import org.nowstart.evergreen.backtest.model.GridSearchRow;
import org.nowstart.evergreen.backtest.model.StrategyParams;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.PriorityQueue;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.ForkJoinPool;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicLong;
import java.util.stream.LongStream;

@Service
public class GridSearchService {

    private static final Logger log = LoggerFactory.getLogger(GridSearchService.class);

    private final BacktestService backtestService;

    public GridSearchService(BacktestService backtestService) {
        this.backtestService = backtestService;
    }

    public List<GridSearchRow> search(List<CandleBar> dailyBars, BacktestConfig config) {
        List<Integer> maLenValues = config.resolveMaLenValues();
        List<Integer> atrPeriodValues = config.resolveAtrPeriodValues();
        List<Double> atrTrailValues = config.resolveAtrTrailMultipliers();
        List<Double> regimeBandValues = config.resolveRegimeBandValues();

        int[] sizes = {
                maLenValues.size(),
                atrPeriodValues.size(),
                atrTrailValues.size(),
                regimeBandValues.size()
        };

        long totalLong = config.combinationCount();
        long[] strides = buildStrides(sizes);
        Comparator<GridSearchRow> better = rankingComparator();
        int topK = config.topK();
        long startedAtNanos = System.nanoTime();
        long logIntervalNanos = TimeUnit.SECONDS.toNanos(config.gridProgressLogSeconds());
        AtomicLong processed = new AtomicLong(0L);
        AtomicLong nextLogAtNanos = new AtomicLong(startedAtNanos + logIntervalNanos);

        PriorityQueue<GridSearchRow> heap = runGridSearch(
                totalLong,
                config.gridParallelism(),
                topK,
                better,
                index -> {
                    int c0 = coord(index, strides[0], sizes[0]);
                    int c1 = coord(index, strides[1], sizes[1]);
                    int c2 = coord(index, strides[2], sizes[2]);
                    int c3 = coord(index, strides[3], sizes[3]);

                    StrategyParams params = new StrategyParams(
                            config.feePerSide(),
                            config.slippage(),
                            maLenValues.get(c0),
                            atrPeriodValues.get(c1),
                            atrTrailValues.get(c2),
                            regimeBandValues.get(c3)
                    );
                    GridSearchRow row = toGridRow(dailyBars, params);
                    logProgress(processed, nextLogAtNanos, logIntervalNanos, totalLong, startedAtNanos);
                    return row;
                }
        );

        logProgressFinal(processed.get(), totalLong, startedAtNanos);

        List<GridSearchRow> out = new ArrayList<>(heap);
        out.sort(better);
        return List.copyOf(out);
    }

    private GridSearchRow toGridRow(List<CandleBar> dailyBars, StrategyParams params) {
        BacktestResult result = backtestService.backtestDailyStrategy(dailyBars, params);
        double cagr = result.summary().cagr();
        double mdd = result.summary().mdd();
        double calmarLike = mdd == 0.0 ? Double.NaN : cagr / Math.abs(mdd);
        return new GridSearchRow(params, calmarLike, cagr, mdd, result.summary().finalEquity());
    }

    private Comparator<GridSearchRow> rankingComparator() {
        return Comparator
                .comparingDouble((GridSearchRow row) -> rankValue(row.calmarLike())).reversed()
                .thenComparing(Comparator.comparingDouble((GridSearchRow row) -> rankValue(row.cagr())).reversed())
                .thenComparing(Comparator.comparingDouble((GridSearchRow row) -> rankValue(row.finalEquity())).reversed());
    }

    private int coord(long index, long stride, int size) {
        return (int) ((index / stride) % size);
    }

    private long[] buildStrides(int[] sizes) {
        long[] strides = new long[sizes.length];
        long stride = 1L;
        for (int i = sizes.length - 1; i >= 0; i--) {
            strides[i] = stride;
            if (sizes[i] > 0 && stride > Long.MAX_VALUE / sizes[i]) {
                throw new IllegalArgumentException("grid stride overflow");
            }
            stride *= sizes[i];
        }
        return strides;
    }

    private PriorityQueue<GridSearchRow> runGridSearch(
            long total,
            int parallelism,
            int topK,
            Comparator<GridSearchRow> better,
            java.util.function.LongFunction<GridSearchRow> evaluator
    ) {
        if (parallelism <= 1) {
            return searchOnStream(LongStream.range(0, total), topK, better, evaluator);
        }

        ForkJoinPool pool = new ForkJoinPool(parallelism);
        try {
            return pool.submit(() -> searchOnStream(LongStream.range(0, total).parallel(), topK, better, evaluator)).get();
        } catch (InterruptedException e) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("Grid search interrupted", e);
        } catch (ExecutionException e) {
            throw new IllegalStateException("Grid search failed", e.getCause());
        } finally {
            pool.shutdown();
        }
    }

    private PriorityQueue<GridSearchRow> searchOnStream(
            LongStream stream,
            int topK,
            Comparator<GridSearchRow> better,
            java.util.function.LongFunction<GridSearchRow> evaluator
    ) {
        return stream.collect(
                () -> new PriorityQueue<>(topK, better.reversed()),
                (heap, index) -> offerTopK(heap, evaluator.apply(index), topK, better),
                (left, right) -> {
                    for (GridSearchRow row : right) {
                        offerTopK(left, row, topK, better);
                    }
                }
        );
    }

    private void offerTopK(
            PriorityQueue<GridSearchRow> heap,
            GridSearchRow row,
            int topK,
            Comparator<GridSearchRow> better
    ) {
        if (heap.size() < topK) {
            heap.offer(row);
            return;
        }
        GridSearchRow worst = heap.peek();
        if (worst != null && better.compare(row, worst) < 0) {
            heap.poll();
            heap.offer(row);
        }
    }

    private double rankValue(double value) {
        return Double.isFinite(value) ? value : Double.NEGATIVE_INFINITY;
    }

    private void logProgress(
            AtomicLong processed,
            AtomicLong nextLogAtNanos,
            long logIntervalNanos,
            long total,
            long startedAtNanos
    ) {
        long done = processed.incrementAndGet();
        long now = System.nanoTime();
        long targetNanos = nextLogAtNanos.get();
        if (now < targetNanos) {
            return;
        }
        if (!nextLogAtNanos.compareAndSet(targetNanos, now + logIntervalNanos)) {
            return;
        }

        double elapsedSec = Math.max(1e-9, (System.nanoTime() - startedAtNanos) / 1_000_000_000.0);
        double rate = done / elapsedSec;
        double pct = (done * 100.0) / Math.max(1L, total);
        log.info(
                "[Grid][Progress] done={}/{} ({}%) rate={}/s",
                done,
                total,
                String.format(Locale.US, "%.2f", pct),
                Math.round(rate)
        );
    }

    private void logProgressFinal(long done, long total, long startedAtNanos) {
        double elapsedSec = Math.max(1e-9, (System.nanoTime() - startedAtNanos) / 1_000_000_000.0);
        double rate = done / elapsedSec;
        log.info(
                "[Grid][Done] done={}/{} elapsedSec={} rate={}/s",
                done,
                total,
                String.format(Locale.US, "%.2f", elapsedSec),
                Math.round(rate)
        );
    }
}
