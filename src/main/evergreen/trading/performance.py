"""Read-only account-lifetime accounting reconstructed from the execution journal."""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from evergreen.trading.upbit import Order


def amount(value: object) -> Decimal:
    result = Decimal(str(value))
    if not result.is_finite() or result < 0:
        raise ValueError("Invalid accounting amount")
    return result


@dataclass
class Ledger:
    """Never controls orders or modifies execution state. Missing evidence stays unknown."""

    identity: str
    cursor: int = 0
    reason: str | None = None
    started_at: str | None = None
    capital: Decimal | None = None
    cash: Decimal = Decimal(0)
    btc: Decimal = Decimal(0)
    basis: Decimal = Decimal(0)
    realized: Decimal = Decimal(0)
    fees: Decimal = Decimal(0)
    sequence: int = 0

    def consume(self, event: str, payload: str, timestamp: str) -> None:
        if self.reason:
            return
        try:
            data = json.loads(payload)
            state = data["state"]
            if state["identity"] != self.identity:
                raise ValueError("Account changed")
            if event == "initialized":
                if self.capital is not None or amount(state["btc"]) != 0:
                    raise ValueError("Unknown opening inventory")
                self.capital = self.cash = amount(state["krw"])
                if self.capital == 0 or state["order_sequence"] != 0:
                    raise ValueError("Unknown opening capital")
                start = datetime.fromisoformat(timestamp)
                if start.utcoffset() is None:
                    raise ValueError("Missing timezone")
                self.started_at = start.astimezone(UTC).isoformat()
                return
            if event != "order-terminal":
                self.reason = "balance_discrepancy"
                return
            if self.capital is None or state["order_sequence"] != self.sequence + 1:
                raise ValueError("Incomplete order history")
            order = Order.model_validate(data["detail"])
            if order.state not in ("done", "cancel") or order.trades is None:
                raise ValueError("Not a settled order")
            quantity = sum((fill.volume for fill in order.trades), Decimal(0))
            funds = sum((fill.funds for fill in order.trades), Decimal(0))
            if quantity != order.executed_volume:
                raise ValueError("Incomplete fills")
            if order.side == "bid":
                self.cash -= funds + order.paid_fee
                self.btc += quantity
                if quantity == 0:
                    self.realized -= order.paid_fee
                else:
                    self.basis += funds + order.paid_fee
            else:
                if quantity > self.btc:
                    raise ValueError("Unknown acquisition cost")
                cost = self.basis * quantity / self.btc if self.btc else Decimal(0)
                self.basis -= cost
                self.btc -= quantity
                self.cash += funds - order.paid_fee
                self.realized += funds - order.paid_fee - cost
            self.fees += order.paid_fee
            self.sequence += 1
            if not self.matches(amount(state["krw"]), amount(state["btc"]), self.sequence):
                raise ValueError("Settlement differs from journal")
        except (ValueError, TypeError, KeyError, InvalidOperation):
            self.reason = "incomplete_history"

    def matches(self, cash: Decimal, btc: Decimal, sequence: int) -> bool:
        return (
            self.capital is not None
            and sequence == self.sequence
            and abs(cash - self.cash) <= Decimal(".01")
            and abs(btc - self.btc) <= Decimal(".00000001")
        )

    def snapshot(
        self,
        *,
        cash: Decimal,
        btc: Decimal,
        sequence: int,
        bid: Decimal,
        sell_fee: Decimal,
        now: datetime,
        strategy: str,
    ) -> dict[str, object]:
        reason = self.reason
        if reason is None and not self.matches(cash, btc, sequence):
            reason = "incomplete_history"
        net_inventory = btc * bid * (1 - sell_fee)
        equity = cash + net_inventory
        valid = reason is None and self.capital is not None
        return {
            "schema_version": 1,
            "event": "account_performance",
            "event_id": f"performance:{now.astimezone(UTC).isoformat()}",
            "observed_at": now.astimezone(UTC).isoformat(),
            "event_time": now.timestamp(),
            "market": "KRW-BTC",
            "mode": "live",
            "strategy": strategy,
            "scope": "account_lifetime",
            "status": "complete" if valid else "unavailable",
            "reason": reason,
            "started_at": self.started_at,
            "equity_krw": str(equity),
            "initial_capital_krw": str(self.capital) if valid else None,
            "realized_pnl_krw": str(self.realized) if valid else None,
            "unrealized_pnl_krw": str(net_inventory - self.basis) if valid else None,
            "total_pnl_krw": str(equity - self.capital) if valid and self.capital else None,
            "total_return_pct": str((equity / self.capital - 1) * 100)
            if valid and self.capital
            else None,
            "fees_krw": str(self.fees) if valid else None,
        }
