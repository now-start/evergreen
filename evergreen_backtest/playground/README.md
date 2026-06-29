# Evergreen Backtest Playground

`backtest_playground.ipynb`는 `evergreen_backtest` 프레임워크의 수동 실행/비교용 플레이그라운드다.

```bash
uv sync
uv run jupyter lab evergreen_backtest/playground/backtest_playground.ipynb
```

프레임워크 코드와 같은 디렉터리 트리 안에 두어 v1~v6 전략, interval 자동 선택, 워크포워드 결과를 같은 기준으로 확인한다.
