"""evergreen_lab — 최소한의 범용 스팟(spot) 백테스트/평가 프레임워크.

설계 목표
------------
* 전략 추가는 ``strategies/``에 파일 하나만 놓으면 끝 — 프레임워크 수정 불필요.
* 노트북에서 세 줄로 평가: 고르고, 돌리고, 수익을 본다.
* 바(bar) 단위로 동작하고 포지션을 아는 단일 전략 인터페이스. 실거래 Java
  엔진과 형태가 같아서 검증된 전략을 손으로 1:1 그대로 옮길 수 있다(JSON
  "계약" 없음, weight export 없음, parity harness 없음).

레이어
------
* ``core``       — Candle, Action, Position, BarContext, Strategy, registry.
* ``indicators`` — 인과적(미래를 보지 않는) 기술 지표.
* ``engine``     — 스팟 백테스트 엔진(open->open 체결), 비용 모델, 지표.
* ``data``       — 캔들 로딩(검증된 Upbit fetch + CSV 캐시를 재사용).
* ``strategies`` — 전략마다 파일 하나; import 시 자동 등록.
* ``api``        — ``evaluate(...)`` 노트북 진입점.
"""

from __future__ import annotations

from evergreen_lab.core import Action, BarContext, Candle, Position, Strategy
from evergreen_lab.core.registry import create, list_strategies, register
from evergreen_lab.engine import BacktestResult, BacktestRow, Cost, Summary, run_backtest
from evergreen_lab.api import evaluate, evaluate_candles

# strategies 패키지를 import하면 부작용으로 registry가 채워진다.
from evergreen_lab import strategies as strategies  # noqa: F401,E402

# 여러 전략 병렬 평가 + 진행 바 (registry가 채워진 뒤 import).
from evergreen_lab.analysis.compare import evaluate_strategies  # noqa: E402

__all__ = [
    "Action", "BarContext", "BacktestResult", "BacktestRow", "Candle", "Cost",
    "Position", "Strategy", "Summary", "create", "evaluate", "evaluate_candles",
    "evaluate_strategies", "list_strategies", "register", "run_backtest",
]
