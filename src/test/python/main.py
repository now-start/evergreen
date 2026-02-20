"""Entry point for running v1/v2 strategy samples in IntelliJ."""

from v1 import V1Input, decide_position_v1
from v2 import MarketRegime, V2Input, decide_units_v2


def main() -> None:
    v1_input = V1Input(
        close=101.0,
        ma=100.0,
        rsi=35.0,
        rsi_buy=40.0,
        ma_slope_up=True,
        prev_position=0,
    )
    v2_input = V2Input(
        previous_regime=MarketRegime.BEAR,
        current_regime=MarketRegime.BULL,
        atr_stop_hit=False,
        current_units=0,
        max_units=3,
    )

    print(f"v1 next position: {decide_position_v1(v1_input)}")
    print(f"v2 next units: {decide_units_v2(v2_input)}")


if __name__ == "__main__":
    main()
