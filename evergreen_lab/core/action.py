from __future__ import annotations

from enum import StrEnum


class Action(StrEnum):
    """전략이 바(bar)마다 내리는 결정. 스팟 전용: 진입, 청산, 또는 아무것도 안 함.

    * ``BUY``  — 전량 진입(목표 비율 1.0)으로 이동.
    * ``SELL`` — 전량 청산(목표 비율 0.0)으로 이동.
    * ``HOLD`` — 현재 포지션 유지.

    엔진이 스팟 규칙을 강제한다: 이미 전량 진입 상태에서의 ``BUY``와 이미 청산
    상태에서의 ``SELL``은 둘 다 ``HOLD``로 무너진다.
    """

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"
