import json
from datetime import UTC, datetime
from decimal import Decimal as D

import pytest

from evergreen.trading.performance import Ledger

NOW = datetime(2026, 9, 14, tzinfo=UTC)


def initial(ledger: Ledger, btc: str = "0", capital: str = "1000") -> None:
    ledger.consume(
        "initialized",
        json.dumps(
            {
                "state": {
                    "identity": "test",
                    "krw": capital,
                    "btc": btc,
                    "order_sequence": 0,
                }
            }
        ),
        NOW.isoformat(),
    )


def terminal(
    ledger: Ledger,
    side: str,
    quantity: str,
    funds: str,
    fee: str,
    cash: str,
    btc: str,
    sequence: int,
    status: str = "done",
) -> None:
    ledger.consume(
        "order-terminal",
        json.dumps(
            {
                "state": {"identity": "test", "krw": cash, "btc": btc, "order_sequence": sequence},
                "detail": {
                    "uuid": "private-id",
                    "identifier": "private-intent",
                    "market": "KRW-BTC",
                    "state": status,
                    "side": side,
                    "executed_volume": quantity,
                    "paid_fee": fee,
                    "trades": [{"funds": funds, "volume": quantity}] if D(quantity) else [],
                },
            }
        ),
        NOW.isoformat(),
    )


def snapshot(ledger: Ledger) -> dict[str, object]:
    return ledger.snapshot(
        cash=ledger.cash,
        btc=ledger.btc,
        sequence=ledger.sequence,
        bid=D(120),
        sell_fee=D(".01"),
        now=NOW,
        strategy="example",
    )


def test_fees_partial_sale_and_restart_replay() -> None:
    ledger = Ledger("test")
    initial(ledger)
    terminal(ledger, "bid", "5", "500", "5", "495", "5", 1)
    terminal(ledger, "ask", "2", "240", "2.4", "732.6", "3", 2, "cancel")
    result = snapshot(ledger)
    assert result["status"] == "complete"
    assert D(str(result["realized_pnl_krw"])) == D("35.6")
    assert D(str(result["unrealized_pnl_krw"])) == D("53.4")
    assert D(str(result["total_pnl_krw"])) == D("89")
    assert D(str(result["total_return_pct"])) == D("8.9")
    assert D(str(result["fees_krw"])) == D("7.4")
    assert "private" not in json.dumps(result) and "identity" not in result
    terminal(ledger, "ask", "3", "360", "3.6", "1089", "0", 3)
    assert D(str(snapshot(ledger)["realized_pnl_krw"])) == D(89)
    assert ledger.basis == 0
    # A new process rebuilds the same ledger without writing/resetting trading state.
    other = Ledger("test")
    initial(other)
    terminal(other, "bid", "5", "500", "5", "495", "5", 1)
    terminal(other, "ask", "2", "240", "2.4", "732.6", "3", 2, "cancel")
    terminal(other, "ask", "3", "360", "3.6", "1089", "0", 3)
    assert snapshot(other) == snapshot(ledger)


def test_zero_fill_cancel_and_multiple_buys_average_cost() -> None:
    ledger = Ledger("test")
    initial(ledger)
    terminal(ledger, "bid", "0", "0", "0", "1000", "0", 1, "cancel")
    terminal(ledger, "bid", "2", "200", "2", "798", "2", 2)
    terminal(ledger, "bid", "2", "240", "2.4", "555.6", "4", 3)
    terminal(ledger, "ask", "1", "120", "1.2", "674.4", "3", 4)
    assert ledger.realized == D("7.7")  # 120 - 1.2 - (444.4 / 4)
    assert ledger.basis == D("333.3")


@pytest.mark.parametrize("btc,capital", [("1", "1000"), ("0", "0"), ("0", "NaN")])
def test_unknown_baseline_is_not_zero_profit(btc: str, capital: str) -> None:
    ledger = Ledger("test")
    initial(ledger, btc, capital)
    result = snapshot(ledger)
    assert result["status"] == "unavailable"
    assert result["realized_pnl_krw"] is None and result["total_return_pct"] is None


@pytest.mark.parametrize("payload", ["{", "[]", '{"state": null}'])
def test_malformed_history_does_not_crash_accounting(payload: str) -> None:
    ledger = Ledger("test")
    ledger.consume("initialized", payload, NOW.isoformat())
    assert snapshot(ledger)["status"] == "unavailable"


def test_gap_duplicate_discrepancy_and_missing_history() -> None:
    assert snapshot(Ledger("test"))["total_return_pct"] is None
    for sequence in (0, 2):
        ledger = Ledger("test")
        initial(ledger)
        terminal(ledger, "bid", "1", "100", "1", "899", "1", sequence)
        assert snapshot(ledger)["realized_pnl_krw"] is None
    ledger = Ledger("test")
    initial(ledger)
    ledger.consume(
        "unexpected-balance", json.dumps({"state": {"identity": "test"}}), NOW.isoformat()
    )
    assert snapshot(ledger)["reason"] == "balance_discrepancy"
    ledger = Ledger("test")
    initial(ledger)
    terminal(ledger, "bid", "1", "100", "1", "900", "1", 1)
    assert snapshot(ledger)["status"] == "unavailable"


def test_current_balance_mismatch_cannot_show_stale_profit() -> None:
    ledger = Ledger("test")
    initial(ledger)
    result = ledger.snapshot(
        cash=D(1100),
        btc=D(0),
        sequence=0,
        bid=D(120),
        sell_fee=D(".01"),
        now=NOW,
        strategy="example",
    )
    assert result["total_return_pct"] is None
