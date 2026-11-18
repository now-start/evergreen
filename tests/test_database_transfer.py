import json
from pathlib import Path

import pytest

from evergreen.database.transfer import Snapshot, checksum, main, read_snapshot
from evergreen.trading.state import State


def sample() -> Snapshot:
    state = State(krw="1000", btc="0")  # type: ignore[arg-type]
    return Snapshot.model_validate(
        {
            "format": 1,
            "source_revision": "0002_remove_key_identity",
            "source_max_id": 160968,
            "source_next_id": 160969,
            "state": {"id": 1, "payload": state.model_dump_json()},
            "events": [
                {
                    "id": 1,
                    "time": "2026-09-08T22:30:00+00:00",
                    "event": "initialization-approved",
                    "payload": "{}",
                },
                {
                    "id": 3,
                    "time": "2026-09-08T22:36:00+00:00",
                    "event": "initialized",
                    "payload": json.dumps({"state": state.model_dump(mode="json"), "detail": None}),
                },
            ],
        }
    )


def test_snapshot_checksum_and_expected_count(tmp_path: Path) -> None:
    data = sample().model_dump()
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps({"data": data, "sha256": checksum(data)}), encoding="utf-8")
    assert read_snapshot(path, 2) == sample()
    with pytest.raises(ValueError):
        read_snapshot(path, 16)
    assert main([str(path), "--expected-events", "2", "--check-only"]) == 0
    path.write_text(path.read_text().replace("160968", "160967"), encoding="utf-8")
    with pytest.raises(ValueError):
        read_snapshot(path, 2)


@pytest.mark.parametrize(
    "change", ["duplicate", "unknown", "sequence", "pending", "missing_financial"]
)
def test_snapshot_rejects_incomplete_or_unclassified_history(change: str) -> None:
    data = sample().model_dump()
    if change == "duplicate":
        data["events"].append(data["events"][0])
    elif change == "unknown":
        data["events"][1]["event"] = "valuation"
    elif change == "sequence":
        data["source_next_id"] = 4
    elif change == "pending":
        state = json.loads(data["state"]["payload"])
        state["pending"] = {"identifier": "in-flight"}
        data["state"]["payload"] = json.dumps(state)
    else:
        data["events"].pop()
    with pytest.raises(ValueError):
        Snapshot.model_validate(data)
