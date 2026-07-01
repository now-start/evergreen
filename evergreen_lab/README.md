# evergreen_lab

A minimal, generic **spot** backtest / evaluation framework. Add a strategy, run
it over history in a notebook, read the profit. Strategies migrate to the live
Java engine by hand (no JSON contract / weight export).

## Evaluate in a notebook (3 lines)

```python
import evergreen_lab as lab
from datetime import datetime, timezone

r = lab.evaluate("trend2", from_dt=datetime(2020, 1, 1, tzinfo=timezone.utc))
print(r.describe())      # total_return / buy&hold / cagr / mdd / win_rate ...
r.plot_equity()          # strategy vs buy & hold
```

`lab.list_strategies()` shows everything registered. Tune params inline:
`lab.evaluate("trend2", buy_cutoff=0.08, cost=lab.Cost(fee_per_side=0.0005))`.

Offline (your own candle list, no network): `lab.evaluate_candles("trend2", candles)`.

## Add a strategy (one file)

Create `evergreen_lab/strategies/my_strategy.py`:

```python
from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


@register("my_strategy")
class MyStrategy(Strategy):
    def warmup(self) -> int:
        return 20                      # bars to skip until indicators are valid

    def features(self, candles):       # optional: precompute causal series ONCE
        close = [c.close for c in candles]
        return {"ma": ind.moving_average(close, 20)}

    def decide(self, ctx: BarContext) -> Action:
        price = ctx.now.close
        ma = ctx.feature("ma")
        if price > ma:
            return Action.BUY
        if price < ma and ctx.in_position:   # spot: only exit while holding
            return Action.SELL
        return Action.HOLD
```

Add `from evergreen_lab.strategies import my_strategy` to `strategies/__init__.py`.
That's it — it now shows up in `list_strategies()` and `evaluate("my_strategy")`.

## The one interface (`decide`)

`decide(ctx) -> Action` runs per bar, sees only info up to `ctx.index` (no
look-ahead), and knows the current `ctx.position`. This is the same shape the
live Java engine uses, so a validated strategy transliterates 1:1.

* `ctx.now` — current candle. `ctx.closes()` — closes up to now.
* `ctx.feature(name)` — value at the current bar of a series from `features()`.
* `ctx.in_position` — are we holding? (exits should guard on this.)
* Return `BUY` (go fully in), `SELL` (go flat), or `HOLD`.

## What the engine does

Spot, single asset. A decision at bar *i* fills at **open[i+1]** (no same-bar
look-ahead). Cost (`Cost`, per side) is charged on turnover. `describe()` reports
`total_return`, `buy_hold_return`, `cagr`, `mdd`, `round_trips`, `win_rate`.

## Test

```bash
python evergreen_lab/tests/test_smoke.py     # or: python -m pytest evergreen_lab/tests
```
