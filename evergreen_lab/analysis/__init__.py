"""코어 백테스트 위에 얹는 선택적 분석 레이어: 하이퍼파라미터 그리드 서치와
walk-forward(아웃오브샘플) 평가. 단순히 "전략을 과거 데이터로 실행"하는
평가에는 필요 없다 — 파라미터를 튜닝하거나 정직한 아웃오브샘플 추정치를
원할 때 사용한다.
"""

from __future__ import annotations

from evergreen_lab.analysis.optimizer import Candidate, grid_search
from evergreen_lab.analysis.walk_forward import WalkForwardResult, WalkForwardWindow, walk_forward

__all__ = ["Candidate", "WalkForwardResult", "WalkForwardWindow", "grid_search", "walk_forward"]
