package org.nowstart.evergreen.controller;

import io.swagger.v3.oas.annotations.Operation;
import io.swagger.v3.oas.annotations.responses.ApiResponse;
import io.swagger.v3.oas.annotations.responses.ApiResponses;
import io.swagger.v3.oas.annotations.tags.Tag;
import jakarta.validation.Valid;
import java.util.List;
import org.nowstart.evergreen.data.dto.BalanceDto;
import org.nowstart.evergreen.data.dto.CreateOrderRequest;
import org.nowstart.evergreen.data.dto.OrderChanceDto;
import org.nowstart.evergreen.data.dto.OrderDto;
import org.nowstart.evergreen.data.dto.SignalExecuteRequest;
import org.nowstart.evergreen.service.TradingExecutionService;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/trading")
@Tag(name = "Trading", description = "잔고 조회, 주문 생성/조회/취소, 시그널 실행 API")
public class TradingController {

    private final TradingExecutionService tradingExecutionService;

    public TradingController(TradingExecutionService tradingExecutionService) {
        this.tradingExecutionService = tradingExecutionService;
    }

    @GetMapping("/balances")
    @Operation(summary = "잔고 조회", description = "업비트 계정 잔고를 조회합니다. currency 파라미터로 통화 필터링이 가능합니다.")
    @ApiResponses({
            @ApiResponse(responseCode = "200", description = "조회 성공")
    })
    public List<BalanceDto> getBalances(@RequestParam(value = "currency", required = false) String currency) {
        return tradingExecutionService.getBalances(currency);
    }

    @GetMapping("/orders/chance")
    @Operation(summary = "주문 가능 정보 조회", description = "특정 마켓의 수수료/잔고/최대 주문 금액 정보를 조회합니다.")
    @ApiResponses({
            @ApiResponse(responseCode = "200", description = "조회 성공")
    })
    public OrderChanceDto getOrderChance(@RequestParam("market") String market) {
        return tradingExecutionService.getOrderChance(market);
    }

    @PostMapping("/orders")
    @Operation(summary = "주문 생성", description = "LIVE/PAPER 모드로 주문을 생성합니다.")
    @ApiResponses({
            @ApiResponse(responseCode = "201", description = "주문 생성 성공"),
            @ApiResponse(responseCode = "400", description = "요청 검증 실패"),
            @ApiResponse(responseCode = "422", description = "주문 제약 위반")
    })
    public ResponseEntity<OrderDto> createOrder(@RequestBody @Valid CreateOrderRequest request) {
        OrderDto order = tradingExecutionService.createOrder(request);
        return ResponseEntity.status(HttpStatus.CREATED).body(order);
    }

    @GetMapping("/orders/{clientOrderId}")
    @Operation(summary = "주문 조회", description = "clientOrderId로 주문 상태를 조회합니다.")
    @ApiResponses({
            @ApiResponse(responseCode = "200", description = "조회 성공"),
            @ApiResponse(responseCode = "404", description = "주문 없음")
    })
    public OrderDto getOrder(@PathVariable String clientOrderId) {
        return tradingExecutionService.getOrder(clientOrderId);
    }

    @PostMapping("/orders/{clientOrderId}/cancel")
    @Operation(summary = "주문 취소", description = "clientOrderId로 주문을 취소합니다.")
    @ApiResponses({
            @ApiResponse(responseCode = "200", description = "취소 성공"),
            @ApiResponse(responseCode = "404", description = "주문 없음"),
            @ApiResponse(responseCode = "409", description = "취소 불가 상태")
    })
    public OrderDto cancelOrder(@PathVariable String clientOrderId) {
        return tradingExecutionService.cancelOrder(clientOrderId);
    }

    @PostMapping("/signal-execute")
    @Operation(summary = "시그널 실행", description = "전략 시그널을 주문으로 변환하여 즉시 실행합니다.")
    @ApiResponses({
            @ApiResponse(responseCode = "201", description = "시그널 실행 성공"),
            @ApiResponse(responseCode = "400", description = "요청 검증 실패"),
            @ApiResponse(responseCode = "422", description = "주문 제약 위반")
    })
    public ResponseEntity<OrderDto> executeSignal(@RequestBody @Valid SignalExecuteRequest request) {
        return ResponseEntity.status(HttpStatus.CREATED)
                .body(tradingExecutionService.executeSignal(request));
    }
}
