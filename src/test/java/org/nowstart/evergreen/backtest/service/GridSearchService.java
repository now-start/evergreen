package org.nowstart.evergreen.backtest.service;

import org.nowstart.evergreen.backtest.config.BacktestConfig;
import org.nowstart.evergreen.backtest.model.BacktestResult;
import org.nowstart.evergreen.backtest.model.CandleBar;
import org.nowstart.evergreen.backtest.model.GridSearchRow;
import org.nowstart.evergreen.backtest.model.StrategyParams;
import org.springframework.stereotype.Service;

import java.util.Comparator;
import java.util.List;
import java.util.stream.IntStream;

@Service
public class GridSearchService {

    private final BacktestService backtestService;

    public GridSearchService(BacktestService backtestService) {
        this.backtestService = backtestService;
    }

    public List<GridSearchRow> search(List<CandleBar> bars, BacktestConfig config) {
        List<Double> rsiValues = config.resolveRsiValues();
        List<Integer> maLenValues = config.resolveMaLenValues();
        List<Integer> maSlopeValues = config.resolveMaSlopeValues();

        int maPlane = maLenValues.size() * maSlopeValues.size();
        int total = rsiValues.size() * maPlane;

        return IntStream.range(0, total)
                .parallel()
                .mapToObj(index -> {
                    int rsiIndex = index / maPlane;
                    int remainder = index % maPlane;
                    int maLenIndex = remainder / maSlopeValues.size();
                    int maSlopeIndex = remainder % maSlopeValues.size();

                    StrategyParams params = new StrategyParams(
                            config.feePerSide(),
                            config.slippage(),
                            rsiValues.get(rsiIndex),
                            maLenValues.get(maLenIndex),
                            maSlopeValues.get(maSlopeIndex)
                    );
                    return toGridRow(bars, params);
                })
                .sorted(rankingComparator())
                .limit(config.topK())
                .toList();
    }

    private GridSearchRow toGridRow(List<CandleBar> bars, StrategyParams params) {
        BacktestResult result = backtestService.backtestDailyStrategy(bars, params);
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

    private double rankValue(double value) {
        return Double.isFinite(value) ? value : Double.NEGATIVE_INFINITY;
    }
}
