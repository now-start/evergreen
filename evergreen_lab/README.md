# evergreen_lab

최소한의 범용 **스팟(spot)** 백테스트/평가 프레임워크입니다. 전략을 추가하고, 노트북에서
과거 데이터로 실행한 뒤, 수익을 확인합니다. 검증된 전략은 손으로 그대로 옮겨 실거래
Java 엔진으로 이식합니다(JSON 계약이나 weight export 없음).

## 노트북에서 평가하기 (3줄)

```python
import evergreen_lab as lab
from datetime import datetime, timezone

r = lab.evaluate("v6", from_dt=datetime(2020, 1, 1, tzinfo=timezone.utc))
print(r.describe())      # total_return / buy&hold / cagr / mdd / win_rate ...
r.plot_equity()          # 전략 vs buy & hold
```

`lab.list_strategies()`로 등록된 전략을 모두 확인할 수 있습니다. 파라미터는 인라인으로
튜닝합니다: `lab.evaluate("v6", buy_cutoff=0.08, cost=lab.Cost(fee_per_side=0.0005))`.

오프라인(직접 가진 캔들 리스트로, 네트워크 없이): `lab.evaluate_candles("v6", candles)`.

## 전략 추가하기 (파일 하나)

`evergreen_lab/strategies/my_strategy.py` 생성:

```python
from evergreen_lab import indicators as ind
from evergreen_lab.core import Action, BarContext, Candle, Strategy
from evergreen_lab.core.registry import register


@register("my_strategy")
class MyStrategy(Strategy):
    def warmup(self) -> int:
        return 20                      # 지표가 유효해지기까지 건너뛸 바 개수

    def features(self, candles):       # 선택: 인과적(causal) 시리즈를 한 번만 미리 계산
        close = [c.close for c in candles]
        return {"ma": ind.moving_average(close, 20)}

    def decide(self, ctx: BarContext) -> Action:
        price = ctx.now.close
        ma = ctx.feature("ma")
        if price > ma:
            return Action.BUY
        if price < ma and ctx.in_position:   # spot: 보유 중일 때만 청산
            return Action.SELL
        return Action.HOLD
```

`strategies/__init__.py`에 `from evergreen_lab.strategies import my_strategy`를 추가하세요.
그러면 끝입니다 — 이제 `list_strategies()`와 `evaluate("my_strategy")`에 나타납니다.

## 단일 인터페이스 (`decide`)

`decide(ctx) -> Action`은 바(bar)마다 실행되며, `ctx.index`까지의 정보만 보고(미래를
미리 보지 않음) 현재 `ctx.position`을 알 수 있습니다. 실거래 Java 엔진과 동일한 형태라서
검증된 전략을 1:1로 그대로 옮길 수 있습니다.

* `ctx.now` — 현재 캔들. `ctx.closes()` — 현재까지의 종가들.
* `ctx.feature(name)` — `features()`에서 만든 시리즈의 현재 바 값.
* `ctx.in_position` — 보유 중인지 여부 (청산 로직은 이걸로 가드해야 함).
* `BUY`(전량 진입), `SELL`(전량 청산), `HOLD` 중 하나를 반환합니다.

## 엔진이 하는 일

스팟, 단일 자산. *i*번째 바에서의 결정은 **open[i+1]**에서 체결됩니다(같은 바에서
미리보기 없음). 비용(`Cost`, 편도 기준)은 회전(turnover)에 부과됩니다. `describe()`는
`total_return`, `buy_hold_return`, `cagr`, `mdd`, `round_trips`, `win_rate`를 보여줍니다.

## 테스트

```bash
python evergreen_lab/tests/test_smoke.py     # 또는: python -m pytest evergreen_lab/tests
```

## 전략 목록

| 이름 | 내용 | 인터벌 |
|---|---|---|
| `v6` | 컨퍼런스 우승 "agent_05" trend2 규칙 | `minute_240` |
| `v1` | MA 트렌드 + RSI 과매도 진입, MA 이탈 청산 | `days` |
| `v2` | EMA 레짐 전환 진입 + ATR 트레일링 스탑 청산 | `days` |
| `v3` | v2 레짐 타이밍, 레짐이 BULL을 벗어나면 즉시 청산 (spot이라 사이징은 제외) | `days` |
| `v4` | v2 + 진입 시 주간 EMA 트렌드 필터 추가 | `days` |
| `v5` | v2에 변동성 상태에 따른 ATR 배수를 적용 | `days` |

`lab.evaluate("v2", interval="days", ...)`. `list_strategies()`로 전체를 확인합니다.

## 튜닝 및 아웃오브샘플 (선택적 `analysis` 레이어)

단순히 "과거 데이터로 실행해보기"만 할 때는 필요 없습니다 — 파라미터를 튜닝하거나
정직한 아웃오브샘플 추정치를 원할 때만 사용하세요.

```python
from datetime import datetime, timezone
from evergreen_lab.data import load_candles
from evergreen_lab.analysis import grid_search, walk_forward

candles = load_candles(market="KRW-BTC", from_dt=datetime(2020, 1, 1, tzinfo=timezone.utc), interval="days")

# 파라미터 조합을 Calmar 비율(cagr / |mdd|)로 순위 매김
top = grid_search("v2", candles, {"atr_trail_multiplier": [2, 3, 4], "regime_band": [0.01, 0.02]}, top_k=3)
print(top[0].params, top[0].score)

# walk-forward: 각 학습(train) 구간마다 파라미터를 다시 뽑아 다음의 미관측(unseen)
# 구간에 적용하고, 아웃오브샘플 테스트 구간들을 하나의 연속된 곡선으로 이어붙입니다.
wf = walk_forward("v2", candles, {"atr_trail_multiplier": [2, 3, 4]}, train_size=750, test_size=180)
print(f"OOS total_return={wf.summary.total_return:+.2%} over {len(wf.windows)} windows")
```
