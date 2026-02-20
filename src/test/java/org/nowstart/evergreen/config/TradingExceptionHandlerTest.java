package org.nowstart.evergreen.config;

import feign.FeignException;
import feign.Request;
import feign.Response;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.data.exception.TradingApiException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;

import java.nio.charset.StandardCharsets;
import java.util.Map;

import static org.assertj.core.api.Assertions.assertThat;

class TradingExceptionHandlerTest {

    private final TradingExceptionHandler handler = new TradingExceptionHandler();

    @Test
    void handleTradingApiException_returnsProblemDetailWithCode() {
        TradingApiException exception = new TradingApiException(HttpStatus.NOT_FOUND, "order_not_found", "Order not found");

        ProblemDetail detail = handler.handleTradingApiException(exception);

        assertThat(detail.getStatus()).isEqualTo(HttpStatus.NOT_FOUND.value());
        assertThat(detail.getDetail()).isEqualTo("Order not found");
        assertThat(detail.getProperties()).containsEntry("code", "order_not_found");
    }

    @Test
    void handleUnexpectedException_returnsInternalErrorProblemDetail() {
        ProblemDetail detail = handler.handleUnexpectedException(new IllegalStateException("boom"));

        assertThat(detail.getStatus()).isEqualTo(HttpStatus.INTERNAL_SERVER_ERROR.value());
        assertThat(detail.getDetail()).isEqualTo("Unexpected server error");
        assertThat(detail.getProperties()).containsEntry("code", "internal_error");
    }

    @Test
    void handleFeignException_includesUpstreamBodyAndStatus() {
        Request request = Request.create(
                Request.HttpMethod.GET,
                "/v1/order",
                Map.of(),
                null,
                StandardCharsets.UTF_8,
                null
        );
        Response response = Response.builder()
                .status(400)
                .reason("Bad Request")
                .request(request)
                .headers(Map.of())
                .body("{\"error\":\"invalid_query_payload\"}", StandardCharsets.UTF_8)
                .build();
        FeignException exception = FeignException.errorStatus("UpbitFeignClient#getOrder", response);

        ProblemDetail detail = handler.handleFeignException(exception);

        assertThat(detail.getStatus()).isEqualTo(HttpStatus.BAD_REQUEST.value());
        assertThat(detail.getDetail()).contains("invalid_query_payload");
        assertThat(detail.getProperties()).containsEntry("code", "upbit_error");
        assertThat(detail.getProperties()).containsEntry("upstreamStatus", 400);
    }
}
