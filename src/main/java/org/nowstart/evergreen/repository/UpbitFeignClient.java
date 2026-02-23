package org.nowstart.evergreen.repository;

import java.util.List;
import org.nowstart.evergreen.config.UpbitFeignConfig;
import org.nowstart.evergreen.data.dto.UpbitAccountResponse;
import org.nowstart.evergreen.data.dto.UpbitCreateOrderRequest;
import org.nowstart.evergreen.data.dto.UpbitDayCandleResponse;
import org.nowstart.evergreen.data.dto.UpbitOrderChanceResponse;
import org.nowstart.evergreen.data.dto.UpbitOrderResponse;
import org.nowstart.evergreen.data.dto.UpbitTickerResponse;
import org.springframework.cloud.openfeign.FeignClient;
import org.springframework.web.bind.annotation.DeleteMapping;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;

@FeignClient(
        name = "upbitClient",
        url = "${evergreen.trading.base-url}",
        configuration = UpbitFeignConfig.class
)
public interface UpbitFeignClient {

    @GetMapping("/v1/accounts")
    List<UpbitAccountResponse> getAccounts();

    @GetMapping("/v1/orders/chance")
    UpbitOrderChanceResponse getOrderChance(@RequestParam("market") String market);

    @GetMapping("/v1/ticker")
    List<UpbitTickerResponse> getTickers(@RequestParam("markets") String markets);

    @GetMapping("/v1/candles/days")
    List<UpbitDayCandleResponse> getDayCandles(
            @RequestParam("market") String market,
            @RequestParam("count") int count
    );

    @PostMapping(value = "/v1/orders", consumes = "application/json")
    UpbitOrderResponse createOrder(@RequestBody UpbitCreateOrderRequest request);

    @GetMapping("/v1/order")
    UpbitOrderResponse getOrder(@RequestParam("uuid") String uuid);

    @DeleteMapping("/v1/order")
    UpbitOrderResponse cancelOrder(@RequestParam("uuid") String uuid);

    @GetMapping("/v1/orders")
    List<UpbitOrderResponse> getOpenOrders(
            @RequestParam("market") String market,
            @RequestParam("state") String state
    );
}
