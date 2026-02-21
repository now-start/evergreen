package org.nowstart.evergreen.scheduler;

import static org.mockito.Mockito.verify;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.service.TradingSignalWorkflowService;

@ExtendWith(MockitoExtension.class)
class TradingSignalSchedulerTest {

    @Mock
    private TradingSignalWorkflowService tradingSignalWorkflowService;

    @Test
    void run_delegatesToWorkflowService() {
        TradingSignalScheduler scheduler = new TradingSignalScheduler(tradingSignalWorkflowService);

        scheduler.run();

        verify(tradingSignalWorkflowService).runOnce();
    }
}
