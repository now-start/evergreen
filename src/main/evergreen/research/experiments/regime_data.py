"""Fixed calendar-quarter public datasets; missing candles never become fabricated bars."""

import hashlib
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from pathlib import Path

import httpx
from pydantic import TypeAdapter

from evergreen.market import (
    Candle,
    UpbitCandle,
    collect,
    load_dataset,
    validate_candles,
    write_json,
)


def quarters() -> list[tuple[datetime, datetime]]:
    boundaries = [datetime(y, m, 1, tzinfo=UTC) for y in range(2020, 2027) for m in (1, 4, 7, 10)]
    end = datetime(2026, 9, 1, tzinfo=UTC)
    boundaries = [value for value in boundaries if value < end] + [end]
    return list(pairwise(boundaries))


def collect_quarters(output: Path) -> None:
    output.mkdir(parents=True, exist_ok=False)
    write_json(
        output / "protocol.json",
        {
            "intervals": [[a.isoformat(), b.isoformat()] for a, b in quarters()],
            "warmup_hours": 169,
            "gap_policy": "exclude_quarter_no_interpolation",
            "live_enabled": False,
        },
    )
    records: list[dict[str, object]] = []
    with httpx.Client(follow_redirects=False, trust_env=False) as client:
        for start, end in quarters():
            directory = output / start.date().isoformat()
            print(f"공개 시간봉 수집: {start.date()} ~ {end.date()}", flush=True)
            try:
                collect(client, start - timedelta(hours=169), end, directory)
            except ValueError:
                manifest = json.loads((directory / "quality.json").read_text())
                quality = manifest.get("quality")
                if not quality or not (quality["missing"] or quality["conflicts"]):
                    raise
                records.append(
                    {"start": start.isoformat(), "status": "quality_failed", "quality": quality}
                )
                print(f"분기 품질 실패 보존: {start.date()}", flush=True)
            else:
                candles, digest = load_dataset(directory)
                records.append(
                    {
                        "start": start.isoformat(),
                        "status": "passed",
                        "rows": len(candles),
                        "sha256": digest,
                    }
                )
    write_json(output / "inventory.json", records)


def contiguous_blocks(source: Path, output: Path) -> list[list[Candle]]:
    """Derive all observed continuous blocks, preserving the failed full-quarter experiment."""
    output.mkdir(parents=True, exist_ok=False)
    records: list[Candle] = []
    raw_hashes: dict[str, str] = {}
    for start, _ in quarters():
        directory = source / start.date().isoformat()
        manifest = json.loads((directory / "quality.json").read_text())
        if not manifest["requests"]:
            raise ValueError("공개 API 원본 요청 기록이 없습니다")
        for request in manifest["requests"]:
            name = request["raw"]
            if Path(name).name != name or request["status"] != 200:
                raise ValueError("원본 응답 경로·상태가 잘못됐습니다")
            content = (directory / "raw" / name).read_bytes()
            digest = hashlib.sha256(content).hexdigest()
            if digest != request["sha256"]:
                raise ValueError("원본 캔들 해시 불일치")
            raw_hashes[f"{directory.name}/{name}"] = digest
            payload = json.loads(content, parse_float=Decimal)
            parsed = TypeAdapter(list[UpbitCandle]).validate_python(payload)
            fetched = datetime.fromisoformat(request["fetched_at"])
            records.extend(item.candle(fetched) for item in parsed)
    start = quarters()[0][0] - timedelta(hours=169)
    end = quarters()[-1][1]
    bars, quality = validate_candles(records, start, end)
    if quality.conflicts or quality.incomplete or not bars:
        raise ValueError("원본 가격 충돌·미확정·빈 데이터")
    blocks: list[list[Candle]] = []
    for bar in bars:
        if not blocks or blocks[-1][-1].close_time != bar.open_time:
            blocks.append([])
        blocks[-1].append(bar)
    write_json(
        output / "coverage.json",
        {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "quality": quality.model_dump(mode="json"),
            "raw_sha256": raw_hashes,
            "full_period_passed": False,
            "blocks": [
                {
                    "start": b[0].open_time.isoformat(),
                    "end": b[-1].close_time.isoformat(),
                    "hours": len(b),
                }
                for b in blocks
            ],
        },
    )
    for index, block in enumerate(blocks):
        directory = output / f"block-{index:03d}"
        directory.mkdir()
        content = "".join(bar.model_dump_json() + "\n" for bar in block).encode()
        with (directory / "candles.jsonl").open("xb") as stream:
            stream.write(content)
        write_json(
            directory / "quality.json",
            {
                "status": "passed",
                "sha256": hashlib.sha256(content).hexdigest(),
                "start": block[0].open_time.isoformat(),
                "end": block[-1].close_time.isoformat(),
                "as_of": datetime.now(UTC).isoformat(),
            },
        )
    return blocks


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="실험 08 공개 데이터 수집 · 실계좌 접근 없음")
    parser.add_argument("--output", type=Path, required=True)
    collect_quarters(parser.parse_args().output)
