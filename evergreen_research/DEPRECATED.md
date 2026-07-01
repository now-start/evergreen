# evergreen_research — DEPRECATED (reference only)

This package is **superseded by `evergreen_lab/`**. Keep it for reference; do not
build new strategies here.

## Why it was replaced

`evergreen_research` was built around making Python the runtime *source of truth*
that Java loads: a strategy "contract" JSON, MLP weight export, and a golden
parity harness (`contracts.py` Java-type mirror, `model_export.py`,
`versions.py::contract`, `runner.py::contracts`). The chosen workflow is now
**validate in Python, hand-migrate to Java** — so that machinery is dead weight.

`evergreen_lab` replaces it with:
* one generic, per-bar, position-aware strategy interface (`decide(ctx) -> Action`)
  shaped exactly like the live Java engine, so migration is 1:1 (no JSON contract);
* a spot backtest engine, a strategy registry ("add a file"), and a notebook API.

## What is still used / worth keeping

* `evergreen_research/data.py` — the Upbit fetch + CSV cache. `evergreen_lab/data.py`
  imports `load_bars` from it (the one live dependency). If this package is ever
  removed, move that fetcher into `evergreen_lab` first.
* `models/v1.py … v6.py` — the reference logic the `evergreen_lab/strategies/`
  ports were derived from (v1→v1_ma_rsi, v2→v2_regime_atr, v3→v3_regime_vol
  [spot-adapted], v4→v4_regime_weekly, v5→v5_regime_volstate, v6 rule→trend2).

## Not deleted on purpose

The Java side still references the export format:
`src/main/java/org/nowstart/evergreen/service/strategy/v6/` (`V6ModelConfig`,
`V6ModelBundle`), `model_export.py`, and `V6StrategyParityTest` +
`src/test/resources/strategy-models/v6-golden.json`. Removing this package (or
`model_export.py`) is a separate decision that also touches the Java v6 MLP path,
so it was intentionally left intact rather than deleted here.
