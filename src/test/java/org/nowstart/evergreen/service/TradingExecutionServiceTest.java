package org.nowstart.evergreen.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.assertj.core.api.Assertions.assertThatThrownBy;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.ArgumentMatchers.eq;
import static org.mockito.Mockito.lenient;
import static org.mockito.Mockito.never;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.verifyNoInteractions;
import static org.mockito.Mockito.when;

import java.math.BigDecimal;
import java.time.Duration;
import java.util.List;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;
import org.nowstart.evergreen.data.dto.CreateOrderRequest;
import org.nowstart.evergreen.data.dto.SignalExecuteRequest;
import org.nowstart.evergreen.data.dto.UpbitCreateOrderRequest;
import org.nowstart.evergreen.data.dto.UpbitOrderChanceResponse;
import org.nowstart.evergreen.data.dto.UpbitOrderResponse;
import org.nowstart.evergreen.data.dto.UpbitTickerResponse;
import org.nowstart.evergreen.data.entity.TradingOrder;
import org.nowstart.evergreen.data.exception.TradingApiException;
import org.nowstart.evergreen.data.property.TradingProperties;
import org.nowstart.evergreen.data.type.ExecutionMode;
import org.nowstart.evergreen.data.type.OrderSide;
import org.nowstart.evergreen.data.type.OrderStatus;
import org.nowstart.evergreen.data.type.TradeOrderType;
import org.nowstart.evergreen.repository.AuditEventRepository;
import org.nowstart.evergreen.repository.TradingOrderRepository;
import org.nowstart.evergreen.repository.UpbitFeignClient;

@ExtendWith(MockitoExtension.class)
class TradingExecutionServiceTest {

    @Mock
    private UpbitFeignClient upbitFeignClient;
    @Mock
    private TradingOrderRepository tradingOrderRepository;
    @Mock
    private AuditEventRepository auditEventRepository;
    @Mock
    private OrderReconciliationService orderReconciliationService;
    @Mock
    private PaperExecutionService paperExecutionService;

    @BeforeEach
    void setUp() {
        lenient().when(tradingOrderRepository.save(any(TradingOrder.class))).thenAnswer(invocation -> invocation.getArgument(0));
        lenient().when(paperExecutionService.execute(any(TradingOrder.class))).thenAnswer(invocation -> {
            TradingOrder order = invocation.getArgument(0);
            order.setStatus(OrderStatus.FILLED);
            order.setExecutedVolume(order.getQuantity());
            return order;
        });
        lenient().when(orderReconciliationService.reconcile(any(TradingOrder.class), any(UpbitOrderResponse.class))).thenAnswer(invocation -> {
            TradingOrder order = invocation.getArgument(0);
            UpbitOrderResponse response = invocation.getArgument(1);
            order.setExchangeOrderId(response.uuid());
            order.setStatus(OrderStatus.SUBMITTED);
            return order;
        });
    }

    @Test
    void createOrder_rejectsInvalidSideOrderTypeCombination() {
        TradingExecutionService service = createService();

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_BUY,
                new BigDecimal("0.01"),
                new BigDecimal("50000"),
                ExecutionMode.PAPER,
                "test"
        );

        assertThatThrownBy(() -> service.createOrder(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("SELL side cannot use MARKET_BUY");
    }

    @Test
    void createOrder_rejectsPaperMarketBuyWithoutQuantity() {
        TradingExecutionService service = createService();

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                null,
                new BigDecimal("50000"),
                ExecutionMode.PAPER,
                "test"
        );

        assertThatThrownBy(() -> service.createOrder(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("PAPER MARKET_BUY requires quantity");
    }

    @Test
    void createOrder_rejectsPaperMarketSellWithoutPrice() {
        TradingExecutionService service = createService();

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.5"),
                null,
                ExecutionMode.PAPER,
                "test"
        );

        assertThatThrownBy(() -> service.createOrder(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("PAPER MARKET_SELL requires price");
    }

    @Test
    void createOrder_paperMarketBuyNormalizesUnitPrice() {
        TradingExecutionService service = createService();

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                new BigDecimal("0.01"),
                new BigDecimal("50000"),
                ExecutionMode.PAPER,
                "test"
        );

        service.createOrder(request);

        ArgumentCaptor<TradingOrder> captor = ArgumentCaptor.forClass(TradingOrder.class);
        verify(paperExecutionService).execute(captor.capture());
        TradingOrder normalized = captor.getValue();

        assertThat(normalized.getPrice()).isEqualByComparingTo(new BigDecimal("5000000"));
        assertThat(normalized.getRequestedNotional()).isEqualByComparingTo(new BigDecimal("50000"));
    }

    @Test
    void createOrder_liveMarketBuyWithoutPriceUsesFullKrwBalance() {
        TradingExecutionService service = createService();
        when(upbitFeignClient.getOrderChance("KRW-BTC")).thenReturn(chance("100000", "1", "90000000"));
        when(upbitFeignClient.createOrder(any(UpbitCreateOrderRequest.class))).thenReturn(orderResponse("upbit-uuid-buy-all"));

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                null,
                null,
                ExecutionMode.LIVE,
                "test"
        );

        service.createOrder(request);

        ArgumentCaptor<TradingOrder> orderCaptor = ArgumentCaptor.forClass(TradingOrder.class);
        verify(tradingOrderRepository).save(orderCaptor.capture());
        assertThat(orderCaptor.getValue().getRequestedNotional()).isEqualByComparingTo(new BigDecimal("99950"));
        assertThat(orderCaptor.getValue().getPrice()).isEqualByComparingTo(new BigDecimal("99950"));

        ArgumentCaptor<UpbitCreateOrderRequest> reqCaptor = ArgumentCaptor.forClass(UpbitCreateOrderRequest.class);
        verify(upbitFeignClient).createOrder(reqCaptor.capture());
        assertThat(reqCaptor.getValue().price()).isEqualTo("99950");
        assertThat(reqCaptor.getValue().ord_type()).isEqualTo("price");
    }

    @Test
    void createOrder_liveBuyRejectsInsufficientBalance() {
        TradingExecutionService service = createService();
        when(upbitFeignClient.getOrderChance("KRW-BTC")).thenReturn(chance("1000", "1", "90000000"));

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                null,
                new BigDecimal("50000"),
                ExecutionMode.LIVE,
                "test"
        );

        assertThatThrownBy(() -> service.createOrder(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("Insufficient KRW balance");
    }

    @Test
    void createOrder_liveSellRejectsInsufficientAssetBalance() {
        TradingExecutionService service = createService();
        when(upbitFeignClient.getOrderChance("KRW-BTC")).thenReturn(chance("1000000", "0.1", "10000000"));

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.5"),
                null,
                ExecutionMode.LIVE,
                "test"
        );

        assertThatThrownBy(() -> service.createOrder(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("Insufficient asset balance");
    }

    @Test
    void createOrder_liveMarketSellEstimatesNotionalAndSubmits() {
        TradingExecutionService service = createService();
        when(upbitFeignClient.getOrderChance("KRW-BTC")).thenReturn(chance("1000000", "2", "10000000"));
        when(upbitFeignClient.createOrder(any(UpbitCreateOrderRequest.class))).thenReturn(orderResponse("upbit-uuid-1"));

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.2"),
                null,
                ExecutionMode.LIVE,
                "test"
        );

        service.createOrder(request);

        ArgumentCaptor<TradingOrder> orderCaptor = ArgumentCaptor.forClass(TradingOrder.class);
        verify(tradingOrderRepository).save(orderCaptor.capture());
        assertThat(orderCaptor.getValue().getRequestedNotional()).isEqualByComparingTo(new BigDecimal("2000000"));

        ArgumentCaptor<UpbitCreateOrderRequest> reqCaptor = ArgumentCaptor.forClass(UpbitCreateOrderRequest.class);
        verify(upbitFeignClient).createOrder(reqCaptor.capture());
        UpbitCreateOrderRequest submitted = reqCaptor.getValue();
        assertThat(submitted.ord_type()).isEqualTo("market");
        assertThat(submitted.volume()).isEqualTo("0.2");
        assertThat(submitted.price()).isNull();
    }

    @Test
    void createOrder_liveMarketSellFallsBackToTickerWhenAvgBuyPriceMissing() {
        TradingExecutionService service = createService();
        when(upbitFeignClient.getOrderChance("KRW-BTC")).thenReturn(chance("1000000", "2", "0"));
        when(upbitFeignClient.getTickers("KRW-BTC")).thenReturn(List.of(new UpbitTickerResponse("KRW-BTC", new BigDecimal("51000000"))));
        when(upbitFeignClient.createOrder(any(UpbitCreateOrderRequest.class))).thenReturn(orderResponse("upbit-uuid-2"));

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.2"),
                null,
                ExecutionMode.LIVE,
                "test"
        );

        service.createOrder(request);

        ArgumentCaptor<TradingOrder> orderCaptor = ArgumentCaptor.forClass(TradingOrder.class);
        verify(tradingOrderRepository).save(orderCaptor.capture());
        assertThat(orderCaptor.getValue().getRequestedNotional()).isEqualByComparingTo(new BigDecimal("10200000"));
    }

    @Test
    void createOrder_liveMarketSellRejectsWhenReferencePriceUnavailable() {
        TradingExecutionService service = createService();
        when(upbitFeignClient.getOrderChance("KRW-BTC")).thenReturn(chance("1000000", "2", "0"));
        when(upbitFeignClient.getTickers("KRW-BTC")).thenReturn(List.of());

        CreateOrderRequest request = new CreateOrderRequest(
                "KRW-BTC",
                OrderSide.SELL,
                TradeOrderType.MARKET_SELL,
                new BigDecimal("0.2"),
                null,
                ExecutionMode.LIVE,
                "test"
        );

        assertThatThrownBy(() -> service.createOrder(request))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("reference price");
    }

    @Test
    void cancelOrder_paperOrderChangesStatusWithoutExchangeCall() {
        TradingExecutionService service = createService();
        TradingOrder order = orderEntity("paper-1", ExecutionMode.PAPER, OrderStatus.SUBMITTED, null);
        when(tradingOrderRepository.findByClientOrderId("paper-1")).thenReturn(java.util.Optional.of(order));

        var result = service.cancelOrder("paper-1");

        assertThat(result.status()).isEqualTo(OrderStatus.CANCELED);
        verify(upbitFeignClient, never()).cancelOrder(any());
    }

    @Test
    void cancelOrder_liveWithoutExchangeIdThrows() {
        TradingExecutionService service = createService();
        TradingOrder order = orderEntity("live-1", ExecutionMode.LIVE, OrderStatus.SUBMITTED, null);
        when(tradingOrderRepository.findByClientOrderId("live-1")).thenReturn(java.util.Optional.of(order));

        assertThatThrownBy(() -> service.cancelOrder("live-1"))
                .isInstanceOf(TradingApiException.class)
                .hasMessageContaining("exchange uuid is missing");
    }

    @Test
    void cancelOrder_liveOrderCallsExchangeAndReconcile() {
        TradingExecutionService service = createService();
        TradingOrder order = orderEntity("live-2", ExecutionMode.LIVE, OrderStatus.SUBMITTED, "upbit-live-2");
        when(tradingOrderRepository.findByClientOrderId("live-2")).thenReturn(java.util.Optional.of(order));
        when(upbitFeignClient.cancelOrder("upbit-live-2")).thenReturn(orderResponse("upbit-live-2"));

        service.cancelOrder("live-2");

        verify(upbitFeignClient).cancelOrder("upbit-live-2");
        verify(orderReconciliationService).reconcile(eq(order), any(UpbitOrderResponse.class));
    }

    @Test
    void getOrder_liveOrderRefreshesFromExchange() {
        TradingExecutionService service = createService();
        TradingOrder order = orderEntity("live-3", ExecutionMode.LIVE, OrderStatus.SUBMITTED, "upbit-live-3");
        when(tradingOrderRepository.findByClientOrderId("live-3")).thenReturn(java.util.Optional.of(order));
        when(upbitFeignClient.getOrder("upbit-live-3")).thenReturn(orderResponse("upbit-live-3"));

        service.getOrder("live-3");

        verify(upbitFeignClient).getOrder("upbit-live-3");
        verify(orderReconciliationService).reconcile(eq(order), any(UpbitOrderResponse.class));
    }

    @Test
    void getOrder_paperOrderSkipsExchangeRefresh() {
        TradingExecutionService service = createService();
        TradingOrder order = orderEntity("paper-2", ExecutionMode.PAPER, OrderStatus.FILLED, null);
        when(tradingOrderRepository.findByClientOrderId("paper-2")).thenReturn(java.util.Optional.of(order));

        service.getOrder("paper-2");

        verifyNoInteractions(upbitFeignClient);
    }

    @Test
    void executeSignal_buildsSignalReasonPrefix() {
        TradingExecutionService service = createService();

        SignalExecuteRequest request = new SignalExecuteRequest(
                "KRW-BTC",
                OrderSide.BUY,
                TradeOrderType.MARKET_BUY,
                new BigDecimal("0.01"),
                new BigDecimal("50000"),
                ExecutionMode.PAPER,
                "2026-02-20T00:00:00Z"
        );

        service.executeSignal(request);

        ArgumentCaptor<TradingOrder> captor = ArgumentCaptor.forClass(TradingOrder.class);
        verify(paperExecutionService).execute(captor.capture());
        assertThat(captor.getValue().getReason()).isEqualTo("signal:2026-02-20T00:00:00Z");
    }

    private TradingOrder orderEntity(String clientOrderId, ExecutionMode mode, OrderStatus status, String exchangeOrderId) {
        return TradingOrder.builder()
                .clientOrderId(clientOrderId)
                .symbol("KRW-BTC")
                .side(OrderSide.BUY)
                .orderType(TradeOrderType.MARKET_BUY)
                .mode(mode)
                .quantity(new BigDecimal("0.01"))
                .price(new BigDecimal("50000"))
                .status(status)
                .exchangeOrderId(exchangeOrderId)
                .build();
    }

    private TradingExecutionService createService() {
        TradingProperties properties = new TradingProperties(
                "https://api.upbit.com",
                "",
                "",
                new BigDecimal("0.0005"),
                Duration.ofSeconds(30),
                ExecutionMode.LIVE,
                List.of("KRW-BTC"),
                400,
                true,
                new BigDecimal("100000"),
                "v5"
        );

        return new TradingExecutionService(
                upbitFeignClient,
                tradingOrderRepository,
                auditEventRepository,
                orderReconciliationService,
                paperExecutionService,
                properties,
                new OrderRequestValidationService(),
                new TradingOrderFactory()
        );
    }

    private UpbitOrderChanceResponse chance(String bidBalance, String askBalance, String askAvgBuyPrice) {
        return new UpbitOrderChanceResponse(
                "0.0005",
                "0.0005",
                new UpbitOrderChanceResponse.Account("KRW", bidBalance, "0", "0"),
                new UpbitOrderChanceResponse.Account("BTC", askBalance, "0", askAvgBuyPrice),
                new UpbitOrderChanceResponse.Market(
                        "KRW-BTC",
                        "BTC/KRW",
                        null,
                        null,
                        null,
                        null,
                        new UpbitOrderChanceResponse.MaxTotal("KRW", "100000000")
                )
        );
    }

    private UpbitOrderResponse orderResponse(String uuid) {
        return new UpbitOrderResponse(
                uuid,
                "ask",
                "market",
                null,
                "wait",
                "KRW-BTC",
                "2026-02-20T00:00:00+00:00",
                "0.2",
                "0.2",
                "0",
                "0",
                "0",
                "0",
                "0",
                0,
                List.of()
        );
    }
}
