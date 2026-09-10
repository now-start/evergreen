"""Config Server properties; credentials reuse the existing Spring key names."""

import hashlib
from decimal import Decimal
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import SettingsConfigDict

from evergreen.database.config import DatabaseSettings
from evergreen.strategies.buffer import ID, LIMIT, ExecutionStrategy


class TradingSettings(DatabaseSettings):
    model_config = SettingsConfigDict(env_prefix="EVERGREEN_EXECUTION_", populate_by_name=True)

    live_enabled: bool = False
    strategy: ExecutionStrategy = "breakout-v1"
    poll_seconds: int = Field(default=30, ge=5, le=60)
    max_quote_age_seconds: int = Field(default=5, ge=1, le=30)
    max_signal_age_seconds: int = Field(default=120, ge=1, le=300)
    max_slippage: Decimal = Field(
        default=Decimal(".003"), gt=0, le=Decimal(".01"), allow_inf_nan=False
    )
    max_drawdown: Decimal = Field(default=Decimal(".10"), gt=0, le=LIMIT, allow_inf_nan=False)
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

    @model_validator(mode="after")
    def validate_strategy_risk(self) -> Self:
        if (self.strategy == ID and self.max_drawdown != LIMIT) or (
            self.strategy == "breakout-v1" and self.max_drawdown > Decimal(".10")
        ):
            raise ValueError("early3는 승인된20%, 기존 돌파는 최대10% 위험 기준을 사용합니다")
        return self

    @property
    def candle_count(self) -> int:
        return 200 if self.strategy == ID else 169

    def require_credentials(self) -> None:
        for value in (self.access_key, self.secret_key):
            if not value.get_secret_value().strip() or value.get_secret_value().startswith(
                "{cipher}"
            ):
                raise ValueError("Config Server에서 복호화된 업비트 키가 필요합니다")

    @property
    def identity(self) -> str:
        self.require_credentials()
        return hashlib.sha256(self.access_key.get_secret_value().encode()).hexdigest()
