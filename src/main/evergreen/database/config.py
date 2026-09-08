"""Existing Spring datasource settings shared by web startup and execution."""

from urllib.parse import parse_qs, urlsplit

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(populate_by_name=True)

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
