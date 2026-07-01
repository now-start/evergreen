"""Strategy library. Import each module here so it self-registers on package load.
Add a strategy = drop a file next to these and add one import line below.
"""

from __future__ import annotations

from evergreen_lab.strategies import v6_trend2 as v6_trend2  # noqa: F401  (v6 rule = agent_05 trend2)
from evergreen_lab.strategies import v1_ma_rsi as v1_ma_rsi  # noqa: F401
from evergreen_lab.strategies import v2_regime_atr as v2_regime_atr  # noqa: F401
from evergreen_lab.strategies import v3_regime_vol as v3_regime_vol  # noqa: F401
from evergreen_lab.strategies import v4_regime_weekly as v4_regime_weekly  # noqa: F401
from evergreen_lab.strategies import v5_regime_volstate as v5_regime_volstate  # noqa: F401

__all__ = [
    "v6_trend2",
    "v1_ma_rsi",
    "v2_regime_atr",
    "v3_regime_vol",
    "v4_regime_weekly",
    "v5_regime_volstate",
]
