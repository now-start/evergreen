package org.nowstart.evergreen.scheduler;

import lombok.RequiredArgsConstructor;
import org.nowstart.evergreen.service.TradingSignalWorkflowService;
import org.springframework.cloud.context.config.annotation.RefreshScope;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;

@Service
@RequiredArgsConstructor
public class TradingSignalScheduler {

    private final TradingSignalWorkflowService tradingSignalWorkflowService;

    @Scheduled(fixedDelayString = "${evergreen.trading.interval:30s}")
    public void run() {
        tradingSignalWorkflowService.runOnce();
    }
}
