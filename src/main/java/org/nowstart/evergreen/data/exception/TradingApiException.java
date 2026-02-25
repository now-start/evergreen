package org.nowstart.evergreen.data.exception;

import lombok.Getter;
import org.springframework.http.HttpStatus;

@Getter
public class TradingApiException extends RuntimeException {

    private final HttpStatus status;
    private final String code;

    public TradingApiException(HttpStatus status, String code, String message) {
        super(message);
        this.status = status;
        this.code = code;
    }

}
