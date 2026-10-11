"""Verified KRW cash movements, separate from trading profits and order settlement."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, BaseModel, Field

from evergreen.market import Nonnegative, Positive


class Transfer(BaseModel):
    uuid: str = Field(min_length=1)
    currency: Literal["KRW"]
    type: Literal["deposit", "withdraw"]
    state: str
    amount: Positive
    fee: Nonnegative
    created_at: AwareDatetime
    done_at: AwareDatetime | None

    @property
    def principal(self) -> Decimal:
        return self.amount if self.type == "deposit" else -self.amount

    @property
    def delta(self) -> Decimal:
        return self.principal - self.fee


def completed(rows: list[Transfer], since: datetime, until: datetime) -> list[Transfer]:
    result = []
    seen: set[str] = set()
    for row in rows:
        if row.uuid in seen:
            raise ValueError("Duplicate cash movement")
        seen.add(row.uuid)
        if row.done_at is None or row.done_at <= since:
            continue
        if row.created_at <= since or row.done_at > until:
            raise ValueError("Cash movement crosses an unverified balance boundary")
        if row.done_at < row.created_at:
            raise ValueError("Invalid cash movement time")
        expected = "ACCEPTED" if row.type == "deposit" else "DONE"
        if row.state != expected:
            raise ValueError("Cash movement is not completed")
        result.append(row)
    return result
