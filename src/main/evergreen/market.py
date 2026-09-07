"""Public hourly candles and immutable, quality-checked local datasets."""

import hashlib
import json
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Literal, Self

import httpx
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator, model_validator

HOUR = timedelta(hours=1)
Positive = Annotated[Decimal, Field(gt=0, allow_inf_nan=False)]
Nonnegative = Annotated[Decimal, Field(ge=0, allow_inf_nan=False)]


def utc_hour(value: datetime) -> datetime:
    if value.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    value = value.astimezone(UTC)
    if value.minute or value.second or value.microsecond:
        raise ValueError("timestamp must be aligned to a UTC hour")
    return value


class Candle(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    market: Literal["KRW-BTC"] = "KRW-BTC"
    interval_minutes: Literal[60] = 60
    open_time: datetime
    open: Positive
    high: Positive
    low: Positive
    close: Positive
    volume: Nonnegative
    quote_volume: Nonnegative
    fetched_at: datetime

    _hour = field_validator("open_time")(utc_hour)

    @field_validator("fetched_at")
    @classmethod
    def aware_fetch(cls, value: datetime) -> datetime:
        if value.utcoffset() is None:
            raise ValueError("fetched_at must include a timezone")
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def prices(self) -> Self:
        if not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high:
            raise ValueError("invalid OHLC price bounds")
        return self

    @property
    def close_time(self) -> datetime:
        return self.open_time + HOUR


class Quality(BaseModel):
    rows: int = 0
    duplicates: int = 0
    incomplete: int = 0
    outside_range: int = 0
    conflicts: list[datetime] = Field(default_factory=list)
    missing: list[datetime] = Field(default_factory=list)

    @property
    def valid(self) -> bool:
        return self.rows > 0 and not self.conflicts and not self.missing


def validate_candles(
    candles: Sequence[Candle], start: datetime, end: datetime, *, as_of: datetime | None = None
) -> tuple[list[Candle], Quality]:
    start, end = utc_hour(start), utc_hour(end)
    if end <= start:
        raise ValueError("end must be after start")
    cutoff = as_of or datetime.now(UTC)
    if cutoff.utcoffset() is None:
        raise ValueError("as_of must include a timezone")
    report = Quality()
    unique: dict[datetime, Candle] = {}
    for item in candles:
        if item.close_time > cutoff:
            report.incomplete += 1
            continue
        if not start <= item.open_time < end:
            report.outside_range += 1
            continue
        previous = unique.get(item.open_time)
        if previous is not None:
            if previous.model_dump(exclude={"fetched_at"}) != item.model_dump(
                exclude={"fetched_at"}
            ):
                report.conflicts.append(item.open_time)
            else:
                report.duplicates += 1
        else:
            unique[item.open_time] = item
    cursor = start
    while cursor < end:
        if cursor not in unique:
            report.missing.append(cursor)
        cursor += HOUR
    result = sorted(unique.values(), key=lambda item: item.open_time)
    report.rows = len(result)
    return result, report


class UpbitCandle(BaseModel):
    market: Literal["KRW-BTC"]
    unit: Literal[60]
    candle_date_time_utc: datetime
    opening_price: Positive
    high_price: Positive
    low_price: Positive
    trade_price: Positive
    candle_acc_trade_volume: Nonnegative
    candle_acc_trade_price: Nonnegative

    def candle(self, fetched_at: datetime) -> Candle:
        # Upbit names this field UTC but serializes it without an offset.
        opening = self.candle_date_time_utc
        if opening.tzinfo is None:
            opening = opening.replace(tzinfo=UTC)
        return Candle(
            open_time=opening,
            open=self.opening_price,
            high=self.high_price,
            low=self.low_price,
            close=self.trade_price,
            volume=self.candle_acc_trade_volume,
            quote_volume=self.candle_acc_trade_price,
            fetched_at=fetched_at,
        )


def write_json(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write("\n")


def collect(
    client: httpx.Client,
    start: datetime,
    end: datetime,
    output: Path,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> list[Candle]:
    start, end = utc_hour(start), utc_hour(end)
    as_of = datetime.now(UTC)
    if end <= start or end > as_of:
        raise ValueError("require start < end <= now; both must be closed hour boundaries")
    output.mkdir(parents=True, exist_ok=False)
    raw_dir = output / "raw"
    raw_dir.mkdir()
    records: list[Candle] = []
    requests: list[dict[str, object]] = []
    report: Quality | None = None
    try:
        cursor, page = end, 0
        while cursor > start:
            # 5 requests/sec at most, below the documented 10/sec shared IP limit.
            sleep(0.2)
            response = client.get(
                "https://api.upbit.com/v1/candles/minutes/60",
                params={"market": "KRW-BTC", "count": 200, "to": cursor.isoformat()},
                timeout=15,
            )
            fetched_at = datetime.now(UTC)
            page += 1
            raw_name = f"{page:06d}.json"
            with (raw_dir / raw_name).open("xb") as stream:
                stream.write(response.content)
            requests.append(
                {
                    "to": cursor.isoformat(),
                    "fetched_at": fetched_at.isoformat(),
                    "status": response.status_code,
                    "raw": raw_name,
                    "sha256": hashlib.sha256(response.content).hexdigest(),
                    "remaining_req": response.headers.get("Remaining-Req"),
                }
            )
            # On 429/418 fail closed, retain response and do not continue hammering the API.
            response.raise_for_status()
            payload = json.loads(response.content, parse_float=Decimal)
            batch = TypeAdapter(list[UpbitCandle]).validate_python(payload)
            if not batch:
                break
            parsed = [item.candle(fetched_at) for item in batch]
            next_cursor = min(item.open_time for item in parsed)
            if next_cursor >= cursor:
                raise ValueError("pagination cursor did not move backwards")
            records.extend(parsed)
            cursor = next_cursor
            remaining = response.headers.get("Remaining-Req", "")
            if any(part.strip() == "sec=0" for part in remaining.split(";")):
                sleep(1)
        candles, report = validate_candles(records, start, end, as_of=as_of)
        if not report.valid:
            raise ValueError("candle quality failed: missing or conflicting candles")
        content = "".join(item.model_dump_json() + "\n" for item in candles).encode()
        with (output / "candles.jsonl").open("xb") as stream:
            stream.write(content)
        write_json(
            output / "quality.json",
            {
                "status": "passed",
                "start": start.isoformat(),
                "end": end.isoformat(),
                "as_of": as_of.isoformat(),
                "sha256": hashlib.sha256(content).hexdigest(),
                "quality": report.model_dump(mode="json"),
                "requests": requests,
            },
        )
        return candles
    except (ValueError, httpx.HTTPError, OSError) as error:
        if not (output / "quality.json").exists():
            write_json(
                output / "quality.json",
                {
                    "status": "failed",
                    "error": str(error),
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "requests": requests,
                    "quality": report.model_dump(mode="json") if report else None,
                },
            )
        raise


class _DatasetManifest(BaseModel):
    status: Literal["passed"]
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    start: datetime
    end: datetime
    as_of: datetime

    _hours = field_validator("start", "end")(utc_hour)


def load_dataset(directory: Path) -> tuple[list[Candle], str]:
    content = (directory / "candles.jsonl").read_bytes()
    manifest = _DatasetManifest.model_validate_json((directory / "quality.json").read_bytes())
    digest = hashlib.sha256(content).hexdigest()
    if manifest.sha256 != digest:
        raise ValueError("dataset hash/status mismatch")
    candles = [Candle.model_validate_json(line) for line in content.splitlines()]
    clean, report = validate_candles(candles, manifest.start, manifest.end, as_of=manifest.as_of)
    if not report.valid or report.duplicates or report.outside_range or report.incomplete:
        raise ValueError("dataset quality mismatch")
    return clean, digest
