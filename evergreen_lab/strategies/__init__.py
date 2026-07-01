"""전략 라이브러리. 패키지 로드 시 자동 등록되도록 여기서 각 모듈을 import한다.
전략 추가 = 이 옆에 파일을 하나 놓고 아래에 import 한 줄을 추가.
"""

from __future__ import annotations

from evergreen_lab.strategies import v6_trend2 as v6_trend2  # noqa: F401  (v6 규칙 = agent_05 trend2)
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
