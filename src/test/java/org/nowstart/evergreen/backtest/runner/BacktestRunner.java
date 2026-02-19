package org.nowstart.evergreen.backtest.runner;

import org.nowstart.evergreen.backtest.config.BacktestConfig;
import org.nowstart.evergreen.backtest.model.BacktestResult;
import org.nowstart.evergreen.backtest.model.BacktestRow;
import org.nowstart.evergreen.backtest.model.CandleBar;
import org.nowstart.evergreen.backtest.model.GridSearchRow;
import org.nowstart.evergreen.backtest.model.StrategyParams;
import org.nowstart.evergreen.backtest.service.BacktestService;
import org.nowstart.evergreen.backtest.service.GridSearchService;
import org.nowstart.evergreen.backtest.service.PlotService;
import org.nowstart.evergreen.backtest.service.UpbitDataService;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.ApplicationArguments;
import org.springframework.boot.ApplicationRunner;
import org.springframework.stereotype.Component;

import java.util.List;

@Component
public class BacktestRunner implements ApplicationRunner {

    private static final Logger log = LoggerFactory.getLogger(BacktestRunner.class);
    private static final int TAIL_ROW_COUNT = 10;

    private final BacktestConfig config;
    private final UpbitDataService upbitDataService;
    private final BacktestService backtestService;
    private final GridSearchService gridSearchService;
    private final PlotService plotService;

    public BacktestRunner(
            BacktestConfig config,
            UpbitDataService upbitDataService,
            BacktestService backtestService,
            GridSearchService gridSearchService,
            PlotService plotService
    ) {
        this.config = config;
        this.upbitDataService = upbitDataService;
        this.backtestService = backtestService;
        this.gridSearchService = gridSearchService;
        this.plotService = plotService;
    }

    @Override
    public void run(ApplicationArguments args) {
        if (!config.enabled()) {
            log.info("backtest.enabled=false; pass --backtest.enabled=true to run");
            return;
        }

        logSection("BACKTEST START");
        log.info("[Overview] market={} from={} to={} validationRatio={} topK={}",
                config.market(), config.fromDt(), config.toDt(), config.validationRatio(), config.topK());

        List<CandleBar> bars = upbitDataService.loadBars(config);
        log.info("[Overview] loaded bars={}", bars.size());

        int splitIndex = config.validationSplitIndex(bars.size());
        List<CandleBar> validationBars = bars.subList(0, splitIndex);
        List<CandleBar> testBars = bars.subList(splitIndex, bars.size());

        logSection("DATA SPLIT");
        logSplit(validationBars, testBars);

        logSection("GRID SEARCH (VALID)");
        List<GridSearchRow> rows = logGridSearch(validationBars);
        if (rows.isEmpty()) {
            throw new IllegalStateException("Grid search returned no rows");
        }

        StrategyParams selectedParams = rows.get(0).params();
        BacktestResult validationResult = backtestService.backtestDailyStrategy(validationBars, selectedParams);
        BacktestResult result = backtestService.backtestDailyStrategy(testBars, selectedParams);

        logSection("TOP1 PARAM");
        log.info("[Overview] selected params={}", selectedParams);

        logSection("SUMMARY");
        logSummary("VALID", validationResult);
        logSummary("TEST", result);

        logSection("PLOT");
        plotService.saveCharts(result, config.plotGridLines(), selectedParams);

        logSection("TEST TAIL");
        logTailRows("TEST", result.rows());
        logSection("BACKTEST END");
    }

    private void logSplit(List<CandleBar> validationBars, List<CandleBar> testBars) {
        CandleBar validStart = validationBars.get(0);
        CandleBar validEnd = validationBars.get(validationBars.size() - 1);
        CandleBar testStart = testBars.get(0);
        CandleBar testEnd = testBars.get(testBars.size() - 1);
        log.info("[Overview] validation rows={} range={} -> {}", validationBars.size(), validStart.timestamp(), validEnd.timestamp());
        log.info("[Overview] test rows={} range={} -> {}", testBars.size(), testStart.timestamp(), testEnd.timestamp());
    }

    private void logSummary(String phase, BacktestResult result) {
        log.info("[{}] range={} final={} bh={} cagr={} mdd={} trades={}",
                phase,
                result.summary().range(),
                result.summary().finalEquity(),
                result.summary().finalEquityBh(),
                result.summary().cagr(),
                result.summary().mdd(),
                result.summary().trades());
    }

    private List<GridSearchRow> logGridSearch(List<CandleBar> bars) {
        log.info("[Overview] combinations={} topK={}", config.combinationCount(), config.topK());
        List<GridSearchRow> rows = gridSearchService.search(bars, config);
        for (int i = 0; i < rows.size(); i++) {
            GridSearchRow row = rows.get(i);
            log.info("[Candidate {}/{}] calmarLike={} cagr={} mdd={} final={} params={}",
                    i + 1, rows.size(), row.calmarLike(), row.cagr(), row.mdd(), row.finalEquity(), row.params());
        }
        return rows;
    }

    private void logTailRows(String phase, List<BacktestRow> rows) {
        int start = Math.max(0, rows.size() - TAIL_ROW_COUNT);
        for (int i = start; i < rows.size(); i++) {
            BacktestRow row = rows.get(i);
            log.info("[Backtest][{}][TAIL] ts={} close={} pos={} eq={}", phase, row.timestamp(), row.close(), row.posOpen(), row.equity());
        }
    }

    private void logSection(String title) {
        log.info("========== {} ==========", title);
    }
}
