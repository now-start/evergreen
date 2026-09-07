"""Config Server properties; credentials reuse the existing Spring key names."""

import hashlib
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class TradingSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVERGREEN_EXECUTION_", populate_by_name=True)

    live_enabled: bool = False
    datasource_url: SecretStr = Field(
        default=SecretStr(""), validation_alias="SPRING_DATASOURCE_URL", repr=False, exclude=True
    )
    datasource_username: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="SPRING_DATASOURCE_USERNAME",
        repr=False,
        exclude=True,
    )
    datasource_password: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="SPRING_DATASOURCE_PASSWORD",
        repr=False,
        exclude=True,
    )
    poll_seconds: int = Field(default=30, ge=5, le=60)
    max_quote_age_seconds: int = Field(default=5, ge=1, le=30)
    max_signal_age_seconds: int = Field(default=120, ge=1, le=300)
    max_slippage: Decimal = Field(
        default=Decimal(".003"), gt=0, le=Decimal(".01"), allow_inf_nan=False
    )
    max_drawdown: Decimal = Field(
        default=Decimal(".10"), gt=0, le=Decimal(".10"), allow_inf_nan=False
    )
    access_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="EVERGREEN_TRADING_ACCESS_KEY",
        repr=False,
        exclude=True,
    )
    secret_key: SecretStr = Field(
        default=SecretStr(""),
        validation_alias="EVERGREEN_TRADING_SECRET_KEY",
        repr=False,
        exclude=True,
    )

    def require_credentials(self) -> None:
        for value in (self.access_key, self.secret_key):
            if not value.get_secret_value().strip() or value.get_secret_value().startswith(
                "{cipher}"
            ):
                raise ValueError("Config Server에서 복호화된 업비트 키가 필요합니다")

    @property
    def database_url(self) -> URL:
        raw = self.datasource_url.get_secret_value().removeprefix("jdbc:")
        parsed = urlsplit(raw)
        query = parse_qs(parsed.query, keep_blank_values=True)
        allowed = {
            "useSSL",
            "sslMode",
            "useUnicode",
            "characterEncoding",
            "serverTimezone",
            "allowPublicKeyRetrieval",
            "rewriteBatchedStatements",
            "useLegacyDatetimeCode",
        }
        if (
            parsed.scheme not in {"mysql", "mariadb"}
            or not parsed.hostname
            or not parsed.path.strip("/")
            or parsed.username
            or parsed.password
            or parsed.fragment
            or set(query) - allowed
            or any(len(values) != 1 for values in query.values())
        ):
            raise ValueError("지원되는 단일 MariaDB JDBC 주소가 필요합니다")
        if (
            query.get("useSSL", ["false"])[0].lower() != "false"
            or query.get("sslMode", ["DISABLED"])[0].upper() != "DISABLED"
        ):
            raise ValueError("TLS JDBC 설정은 asyncmy 연결 설정으로 명시적으로 이관해야 합니다")
        user, password = (
            self.datasource_username.get_secret_value(),
            self.datasource_password.get_secret_value(),
        )
        if (
            not user
            or not password
            or user.startswith("{cipher}")
            or password.startswith("{cipher}")
        ):
            raise ValueError("복호화된 기존 datasource 계정이 필요합니다")
        return URL.create(
            "mysql+asyncmy",
            username=user,
            password=password,
            host=parsed.hostname,
            port=parsed.port or 3306,
            database=parsed.path.strip("/"),
            query={"charset": "utf8mb4"},
        )

    @property
    def identity(self) -> str:
        self.require_credentials()
        return hashlib.sha256(self.access_key.get_secret_value().encode()).hexdigest()
