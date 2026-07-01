from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import ClassVar

from evergreen_lab.core.action import Action
from evergreen_lab.core.candle import Candle
from evergreen_lab.core.context import BarContext


class Strategy(ABC):
    """모든 전략의 베이스 클래스. 서브클래싱하고 ``decide``만 구현하면 끝.

    계약(실거래 Java 엔진과 동일해서 1:1 이식 가능):
    * ``warmup()``   — 거래 시작 전 건너뛸 바 개수 (지표가 아직 유효하지 않음).
    * ``features()`` — 선택; 실행당 한 번만 인과적 지표 시리즈를 미리 계산.
    * ``reset()``    — 선택; 실행별 내부 상태(예: 트레일링 스탑 peak)를 초기화.
                        인스턴스를 안전하게 재사용할 수 있도록 엔진이 각 백테스트
                        전에 호출한다.
    * ``decide(ctx)``— 바마다: ``ctx.index``까지의 정보로 BUY / SELL / HOLD를 반환.
    """

    name: ClassVar[str] = ""

    def warmup(self) -> int:
        return 0

    def features(self, candles: Sequence[Candle]) -> dict[str, list[float]]:
        return {}

    def reset(self) -> None:
        return None

    @abstractmethod
    def decide(self, ctx: BarContext) -> Action:
        ...
