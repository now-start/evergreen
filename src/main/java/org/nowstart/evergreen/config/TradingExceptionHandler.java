package org.nowstart.evergreen.config;

import feign.FeignException;
import jakarta.validation.ConstraintViolation;
import jakarta.validation.ConstraintViolationException;
import java.util.List;
import org.nowstart.evergreen.data.exception.TradingApiException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ProblemDetail;
import org.springframework.validation.FieldError;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class TradingExceptionHandler {

    @ExceptionHandler(TradingApiException.class)
    public ProblemDetail handleTradingApiException(TradingApiException exception) {
        ProblemDetail problemDetail = ProblemDetail.forStatusAndDetail(exception.getStatus(), exception.getMessage());
        problemDetail.setProperty("code", exception.getCode());
        return problemDetail;
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ProblemDetail handleValidationException(MethodArgumentNotValidException exception) {
        List<String> details = exception.getBindingResult()
                .getFieldErrors()
                .stream()
                .map(FieldError::getDefaultMessage)
                .toList();

        ProblemDetail problemDetail = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, "Request validation failed");
        problemDetail.setProperty("code", "validation_error");
        problemDetail.setProperty("details", details);
        return problemDetail;
    }

    @ExceptionHandler(ConstraintViolationException.class)
    public ProblemDetail handleConstraintViolationException(ConstraintViolationException exception) {
        List<String> details = exception.getConstraintViolations()
                .stream()
                .map(ConstraintViolation::getMessage)
                .toList();

        ProblemDetail problemDetail = ProblemDetail.forStatusAndDetail(HttpStatus.BAD_REQUEST, "Request validation failed");
        problemDetail.setProperty("code", "validation_error");
        problemDetail.setProperty("details", details);
        return problemDetail;
    }

    @ExceptionHandler(FeignException.class)
    public ProblemDetail handleFeignException(FeignException exception) {
        HttpStatus status = HttpStatus.resolve(exception.status());
        if (status == null) {
            status = HttpStatus.BAD_GATEWAY;
        }

        String detail = extractFeignDetail(exception);
        ProblemDetail problemDetail = ProblemDetail.forStatusAndDetail(status, detail);
        problemDetail.setProperty("code", "upbit_error");
        problemDetail.setProperty("upstreamStatus", exception.status());
        return problemDetail;
    }

    @ExceptionHandler(Exception.class)
    public ProblemDetail handleUnexpectedException() {
        ProblemDetail problemDetail = ProblemDetail.forStatusAndDetail(
                HttpStatus.INTERNAL_SERVER_ERROR,
                "Unexpected server error"
        );
        problemDetail.setProperty("code", "internal_error");
        return problemDetail;
    }

    private String extractFeignDetail(FeignException exception) {
        String body = exception.contentUTF8();
        if (body != null && !body.isBlank()) {
            return body;
        }

        String message = exception.getMessage();
        if (message != null && !message.isBlank()) {
            return message;
        }

        return "Upbit API request failed";
    }
}
