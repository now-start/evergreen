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

    def _connection(self) -> tuple[URL, float]:
        try:
            parsed = urlsplit(self.datasource_url.get_secret_value().removeprefix("jdbc:"))
            query = parse_qs(parsed.query, keep_blank_values=True)
            if (
                parsed.scheme != "postgresql"
                or not parsed.hostname
                or not parsed.path.strip("/")
                or "/" in parsed.path[1:]
                or parsed.username is not None
                or parsed.password is not None
                or parsed.fragment
                or set(query) - {"sslmode", "connectTimeout"}
                or any(len(values) != 1 for values in query.values())
            ):
                raise ValueError
            ssl = query.get("sslmode", ["require"])[0]
            timeout = float(query.get("connectTimeout", ["3"])[0])
            if (
                ssl not in {"disable", "require", "verify-ca", "verify-full"}
                or not 0 < timeout <= 60
            ):
                raise ValueError
            user = self.datasource_username.get_secret_value()
            password = self.datasource_password.get_secret_value()
            if (
                not user
                or not password
                or user.startswith("{cipher}")
                or password.startswith("{cipher}")
            ):
                raise ValueError
            return URL.create(
                "postgresql+asyncpg",
                username=user,
                password=password,
                host=parsed.hostname,
                port=parsed.port or 5432,
                database=parsed.path[1:],
                query={"ssl": ssl},
            ), timeout
        except ValueError:
            raise ValueError("PostgreSQL 주소와 복호화된 datasource 계정을 확인하세요") from None

    @property
    def database_url(self) -> URL:
        return self._connection()[0]

    @property
    def connect_args(self) -> dict[str, object]:
        return {"timeout": self._connection()[1], "target_session_attrs": "read-write"}
