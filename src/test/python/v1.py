"""Version 1 strategy logic: RSI + MA trend filter."""

from dataclasses import dataclass


@dataclass(frozen=True)
class V1Input:
    close: float
    ma: float
    rsi: float
    rsi_buy: float
    ma_slope_up: bool
    prev_position: int


def decide_position_v1(data: V1Input) -> int:
    """Return next position (0 or 1) for v1 strategy.

    Rules:
    - Exit if close < MA.
    - Enter if close > MA, MA slope is up, and RSI < buy threshold.
    - Otherwise keep previous position.
    """
    if any(value != value for value in (data.close, data.ma, data.rsi)):
        return 0

    if data.close < data.ma:
        return 0

    if data.close > data.ma and data.ma_slope_up and data.rsi < data.rsi_buy:
        return 1

    return 1 if data.prev_position > 0 else 0


def main() -> None:
    sample = V1Input(
        close=101.0,
        ma=100.0,
        rsi=35.0,
        rsi_buy=40.0,
        ma_slope_up=True,
        prev_position=0,
    )
    print(f"v1 next position: {decide_position_v1(sample)}")


if __name__ == "__main__":
    main()
