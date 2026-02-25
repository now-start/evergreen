package org.nowstart.evergreen.config;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.when;

import feign.FeignException;
import feign.Request;
import feign.Response;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;
import java.util.Set;
import org.junit.jupiter.api.Test;
import org.nowstart.evergreen.data.exception.TradingApiException;
import org.springframework.core.MethodParameter;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.validation.BeanPropertyBindingResult;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;

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
        ProblemDetail detail = handler.handleUnexpectedException();

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

    @Test
    void handleValidationException_returnsValidationDetails() throws NoSuchMethodException {
        BeanPropertyBindingResult bindingResult = new BeanPropertyBindingResult(new ValidationTarget(), "target");
        bindingResult.addError(new FieldError("target", "name", "name is required"));
        MethodParameter methodParameter = new MethodParameter(
                TradingExceptionHandlerTest.class.getDeclaredMethod("dummyValidationMethod", ValidationTarget.class),
                0
        );
        MethodArgumentNotValidException exception = new MethodArgumentNotValidException(methodParameter, bindingResult);

        ProblemDetail detail = handler.handleValidationException(exception);

        assertThat(detail.getStatus()).isEqualTo(HttpStatus.BAD_REQUEST.value());
        assertThat(detail.getDetail()).isEqualTo("Request validation failed");
        assertThat(detail.getProperties()).containsEntry("code", "validation_error");
        assertThat(detail.getProperties()).containsEntry("details", List.of("name is required"));
    }

    @Test
    void handleConstraintViolationException_returnsValidationDetails() {
        @SuppressWarnings("unchecked")
        ConstraintViolation<Object> violation = (ConstraintViolation<Object>) mock(ConstraintViolation.class);
        when(violation.getMessage()).thenReturn("market is required");

        ProblemDetail detail = handler.handleConstraintViolationException(new ConstraintViolationException(Set.of(violation)));

        assertThat(detail.getStatus()).isEqualTo(HttpStatus.BAD_REQUEST.value());
        assertThat(detail.getDetail()).isEqualTo("Request validation failed");
        assertThat(detail.getProperties()).containsEntry("code", "validation_error");
        assertThat(detail.getProperties()).containsEntry("details", List.of("market is required"));
    }

    @Test
    void handleFeignException_fallsBackToBadGatewayWhenStatusUnknown() {
        FeignException exception = mock(FeignException.class);
        when(exception.status()).thenReturn(520);
        when(exception.contentUTF8()).thenReturn("{\"error\":\"upstream\"}");

        ProblemDetail detail = handler.handleFeignException(exception);

        assertThat(detail.getStatus()).isEqualTo(HttpStatus.BAD_GATEWAY.value());
        assertThat(detail.getDetail()).contains("upstream");
        assertThat(detail.getProperties()).containsEntry("upstreamStatus", 520);
    }

    @Test
    void handleFeignException_usesExceptionMessageWhenBodyBlank() {
        FeignException exception = mock(FeignException.class);
        when(exception.status()).thenReturn(400);
        when(exception.contentUTF8()).thenReturn("   ");
        when(exception.getMessage()).thenReturn("fallback message");

        ProblemDetail detail = handler.handleFeignException(exception);

        assertThat(detail.getStatus()).isEqualTo(HttpStatus.BAD_REQUEST.value());
        assertThat(detail.getDetail()).isEqualTo("fallback message");
    }

    @Test
    void handleFeignException_usesDefaultDetailWhenBodyAndMessageBlank() {
        FeignException exception = mock(FeignException.class);
        when(exception.status()).thenReturn(400);
        when(exception.contentUTF8()).thenReturn(" ");
        when(exception.getMessage()).thenReturn(" ");

        ProblemDetail detail = handler.handleFeignException(exception);

        assertThat(detail.getStatus()).isEqualTo(HttpStatus.BAD_REQUEST.value());
        assertThat(detail.getDetail()).isEqualTo("Upbit API request failed");
    }

    @SuppressWarnings("unused")
    private void dummyValidationMethod(ValidationTarget target) {
    }

    private static final class ValidationTarget {
        private String name;
    }
}
