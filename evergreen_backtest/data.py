from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Protocol


CSV_HEADER = [
    "candle_date_time_utc",
    "opening_price",
    "high_price",
    "low_price",
    "trade_price",
    "candle_acc_trade_volume",
]


@dataclass(frozen=True)
class CandleBar:
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float


class DailyCandleClient(Protocol):
    def list_days(self, *, market: str, to_dt: datetime, count: int) -> list[CandleBar]:
        """Return daily candles sorted newest first, matching Upbit's candle API order."""


class UpbitSdkDailyCandleClient:
    """Thin adapter around the official upbit-sdk client."""

    def __init__(self, client: Any | None = None, *, environment: str = "kr", timeout: float = 20.0) -> None:
        if client is not None:
            self._client = client
            return

        try:
            from upbit import Upbit
        except ImportError as exc:
            raise RuntimeError(
                "upbit-sdk is required. Run `uv sync` before running the backtest."
            ) from exc

        self._client = Upbit(environment=environment, timeout=timeout)

    def list_days(self, *, market: str, to_dt: datetime, count: int) -> list[CandleBar]:
        response = self._client.candles.list_days(
            market=market,
            to=to_dt.astimezone(timezone.utc).isoformat(),
            count=count,
        )
        return [_bar_from_sdk_row(row) for row in _iter_response_items(response)]


class _CsvCandleCache:
    def __init__(self, cache_dir: str | Path) -> None:
        self.cache_dir = Path(cache_dir)

    def path_for(self, *, market: str, from_dt: datetime, to_dt: datetime, interval_key: str = "days") -> Path:
        safe_market = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in market)
        from_key = from_dt.astimezone(timezone.utc).strftime("%Y%m%d")
        to_key = to_dt.astimezone(timezone.utc).strftime("%Y%m%d")
        return self.cache_dir / f"{safe_market}_{from_key}_{to_key}_{interval_key}.csv"

    def load(self, path: Path) -> list[CandleBar]:
        dedup: dict[str, CandleBar] = {}
        with path.open("r", encoding="utf-8", newline="") as fp:
            reader = csv.reader(fp)
            next(reader, None)
            for parts in reader:
                if len(parts) < 6:
                    continue
                ts = parse_upbit_timestamp(parts[0])
                dedup[ts.isoformat()] = CandleBar(
                    timestamp=ts,
                    open=float(parts[1]),
                    high=float(parts[2]),
                    low=float(parts[3]),
                    close=float(parts[4]),
                    volume=float(parts[5]),
                )
        return _sort_oldest_first(dedup.values())

    def save(self, path: Path, bars: Iterable[CandleBar]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="") as fp:
            writer = csv.writer(fp)
            writer.writerow(CSV_HEADER)
            for bar in bars:
                writer.writerow([bar.timestamp.isoformat(), bar.open, bar.high, bar.low, bar.close, bar.volume])


def load_bars(
    *,
    market: str,
    from_dt: datetime,
    to_dt: datetime | None,
    cache_dir: str | Path = "outputs/data/upbit-cache",
    client: DailyCandleClient | None = None,
) -> list[CandleBar]:
    resolved_to_dt = to_dt or datetime.now(timezone.utc)
    cache = _CsvCandleCache(cache_dir)
    cache_path = cache.path_for(market=market, from_dt=from_dt, to_dt=resolved_to_dt)
    if cache_path.exists():
        return cache.load(cache_path)

    bars = fetch_daily_bars(
        client=client or UpbitSdkDailyCandleClient(),
        market=market,
        from_dt=from_dt,
        to_dt=resolved_to_dt,
    )
    cache.save(cache_path, bars)
    return bars


def fetch_daily_bars(
    *,
    client: DailyCandleClient,
    market: str,
    from_dt: datetime,
    to_dt: datetime,
    sleep_seconds: float = 0.15,
) -> list[CandleBar]:
    dedup: dict[str, CandleBar] = {}
    cursor = to_dt.astimezone(timezone.utc)
    previous_oldest: datetime | None = None

    while True:
        batch = client.list_days(market=market, to_dt=cursor, count=200)
        if not batch:
            break

        for bar in batch:
            if from_dt <= bar.timestamp <= to_dt:
                dedup[bar.timestamp.isoformat()] = bar

        oldest = min(bar.timestamp for bar in batch)
        if previous_oldest is not None and oldest >= previous_oldest:
            raise RuntimeError(f"Upbit candle pagination did not progress before {cursor.isoformat()}")
        if oldest <= from_dt:
            break

        previous_oldest = oldest
        cursor = datetime.fromtimestamp(oldest.timestamp() - 1, tz=timezone.utc)
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return _sort_oldest_first(dedup.values())


def parse_upbit_timestamp(raw: str) -> datetime:
    text = raw.strip()
    if text.endswith(("Z", "z")):
        return datetime.fromisoformat(text.replace("Z", "+00:00").replace("z", "+00:00")).astimezone(timezone.utc)
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _iter_response_items(response: Any) -> Iterable[Any]:
    if isinstance(response, dict):
        for key in ("items", "data", "root"):
            if key in response:
                return response[key]
        return [response]
    for attr in ("items", "data", "root"):
        if hasattr(response, attr):
            value = getattr(response, attr)
            if not callable(value):
                return value
    if hasattr(response, "model_dump"):
        dumped = response.model_dump()
        if isinstance(dumped, list):
            return dumped
        if isinstance(dumped, dict):
            for key in ("items", "data", "root"):
                if key in dumped:
                    return dumped[key]
    return response


def _bar_from_sdk_row(row: Any) -> CandleBar:
    def value(*names: str) -> Any:
        for name in names:
            if isinstance(row, dict) and name in row:
                return row[name]
            if hasattr(row, name):
                return getattr(row, name)
        raise KeyError(f"missing expected candle field; tried {names}")

    return CandleBar(
        timestamp=parse_upbit_timestamp(str(value("candle_date_time_utc"))),
        open=float(value("opening_price", "open")),
        high=float(value("high_price", "high")),
        low=float(value("low_price", "low")),
        close=float(value("trade_price", "close")),
        volume=float(value("candle_acc_trade_volume", "volume")),
    )


def _sort_oldest_first(bars: Iterable[CandleBar]) -> list[CandleBar]:
    out = list(bars)
    out.sort(key=lambda row: row.timestamp)
    return out
