// Offline contract check. Pass a temporary node_modules directory containing
// @grafana/data@13.2.1 and rxjs; this does not access Grafana, Loki, or accounts.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const modules = process.argv[2];
if (!modules) throw new Error('Usage: node verify-loki-dashboard.cjs /absolute/node_modules');
const g = require(path.join(modules, '@grafana/data'));
const { firstValueFrom } = require(path.join(modules, 'rxjs'));
global.window = {};
for (const t of Object.values(g.standardTransformers)) {
  if (!g.standardTransformersRegistry.getIfExists(t.id)) {
    g.standardTransformersRegistry.register({ id: t.id, name: t.name, transformation: () => t });
  }
}
const dashboard = JSON.parse(fs.readFileSync(path.join(__dirname, 'early3-loki-dashboard.json')));
const flatten = panels => panels.flatMap(panel => [panel, ...flatten(panel.panels || [])]);
const panels = flatten(dashboard.panels);
assert.equal(dashboard.uid, 'evergreen-trading-plot');
assert.equal(dashboard.refresh, '');
assert(!JSON.stringify(dashboard).includes('DS_MARIADB'));
const chart = panels.find(p => p.id === 101);
assert.equal(chart.fieldConfig.defaults.custom.spanNulls, false);
for (const name of ['매수 VWAP', '매도 VWAP']) {
  const props = chart.fieldConfig.overrides.find(o => o.matcher.options === name).properties;
  assert(props.some(p => p.id === 'custom.lineWidth' && p.value === 0));
}
const first = '2026-09-14T00:00:00+00:00';
const second = '2026-09-14T01:00:00+00:00';
const bar = (id, time, close, stop, observed) => ({
  event_id: id, bar_close_time: time, observed_at: observed, close,
  entry_threshold: '101', initial_stop: stop, trailing_stop: null,
  channel_exit_threshold: null, position: 'cash', candidate_signal: null,
  pre_order_signal: null, breach_count: 0, rising_grace: false,
  awaiting_reset: false, exit_due: false, exit_reserved: false, halted: false,
});
const bars = [
  bar('bar:2', second, '103', null, '2026-09-14T02:00:02+00:00'),
  bar('bar:1', first, '100', null, '2026-09-14T02:00:01+00:00'),
  bar('bar:1', first, '99', '90', '2026-09-14T02:00:00+00:00'),
];
const order = (id, time, price, side) => ({
  event_id: id, filled_at: time, fill_price: price, side,
  observed_at: '2026-09-14T02:00:00+00:00', order_sequence: 1,
  quantity: '0.12345678', fee_amount: '1.234', remaining_btc: '0',
  terminal_state: 'cancel', signal_time: first,
});
// Model the documented Extract fields(JSON, replace=true, keepTime=false)
// output. The remaining pipeline runs the real Grafana transform operators.
function extracted(refId, records) {
  const keys = [...new Set(records.flatMap(Object.keys))];
  return { refId, length: records.length, fields: keys.map(name => {
    const values = records.map(r => r[name]);
    return { name, values, config: {}, type: g.getFieldTypeFromValue(values.find(v => v != null)) };
  }) };
}
async function run(panel, frames) {
  assert.deepEqual(panel.transformations[0], {
    id: 'extractFields', options: { source: 'Line', format: 'json', replace: true, keepTime: false },
  });
  return firstValueFrom(g.transformDataFrame(panel.transformations.slice(1), frames));
}
const field = (frame, name) => frame.fields.find(f => g.getFieldDisplayName(f) === name).values;
(async () => {
  const out = await run(chart, [extracted('A', bars),
    extracted('B', [order('buy:1', first, '100.25', 'buy'), order('buy:1', first, '100.25', 'buy')]),
    extracted('C', [order('sell:1', second, '103.50', 'sell')])]);
  const a = out.find(f => f.refId === 'A');
  assert.deepEqual(field(a, 'Time'), [Date.parse(first), Date.parse(second)]);
  assert.deepEqual(field(a, '종가'), [100, 103]);
  assert.deepEqual(field(a, '초기 손절선'), [null, null]);
  assert.deepEqual(field(a, '추적 손절선'), [null, null]);
  assert.deepEqual(field(out.find(f => f.refId === 'B'), '매수 VWAP'), [100.25]);
  assert.deepEqual(field(out.find(f => f.refId === 'C'), '매도 VWAP'), [103.5]);
  const decisions = await run(panels.find(p => p.id === 102), [extracted('A', bars)]);
  assert.deepEqual(field(decisions[0], 'Time'), [Date.parse(second), Date.parse(first)]);
  assert.deepEqual(field(decisions[0], '거래 중단'), [false, false]);
  const orders = await run(panels.find(p => p.id === 103),
    [extracted('A', [order('buy:1', first, '100.25', 'buy')])]);
  assert.deepEqual(field(orders[0], '체결 BTC'), [0.12345678]);
  assert.deepEqual(field(orders[0], '수수료 KRW'), [1.234]);
  assert.deepEqual(field(orders[0], '종료 상태'), ['cancel']);
  assert.deepEqual(await run(chart, []), []);
  const positionCard = panels.find(p => p.id === 122);
  const position = await run(positionCard, [extracted('A', bars)]);
  assert.deepEqual(position[0].fields.map(f => g.getFieldDisplayName(f)), ['Time', '보유 상태']);
  assert.equal(field(position[0], 'Time')[0], Date.parse(second));
  assert.equal(field(position[0], '보유 상태')[0], 'cash');
  for (const id of [121, 122, 123]) {
    const card = panels.find(p => p.id === id);
    assert.deepEqual(card.options.reduceOptions.calcs, ['first']);
    assert.deepEqual(await run(card, []), []);
  }
  const worker = panels.find(p => p.id === 120);
  assert.equal(worker.targets[0].maxLines, 1);
  assert(!worker.targets[0].expr.includes('or vector(0)'));
  const input = { refId: 'A', length: 1, fields: [
    { name: 'Time', type: 'time', values: [Date.parse(first)], config: {} },
    { name: 'Line', type: 'string', values: ['worker_failed'], config: {} },
  ] };
  const workerOut = await firstValueFrom(g.transformDataFrame(worker.transformations, [input]));
  assert.deepEqual(field(workerOut[0], '이벤트'), ['worker_failed']);
  assert.deepEqual(field(workerOut[0], 'Time'), [Date.parse(first)]);
  const performance = {
    observed_at: second, started_at: first, status: 'complete', reason: null,
    realized_pnl_krw: '35.6', unrealized_pnl_krw: '53.4', total_pnl_krw: '89',
    total_return_pct: '8.9', initial_capital_krw: '1000', equity_krw: '1089', fees_krw: '7.4',
  };
  for (const [id, key, title, value] of [
    [130, 'realized_pnl_krw', '누적 실현손익', 35.6],
    [131, 'unrealized_pnl_krw', '미실현손익 · 추정', 53.4],
    [132, 'total_pnl_krw', '총손익 · 추정', 89],
    [133, 'total_return_pct', '원금 대비 총수익률 · 추정', 8.9],
  ]) {
    const panel = panels.find(p => p.id === id);
    assert.equal(panel.targets[0].maxLines, 1);
    assert.deepEqual(panel.options.reduceOptions.calcs, ['first']);
    assert.deepEqual(field((await run(panel, [extracted('A', [performance])]))[0], title), [value]);
    assert.deepEqual(field((await run(panel, [extracted('A', [{...performance, [key]: null}])]))[0], title), [null]);
    assert.deepEqual(await run(panel, []), []);
  }
  const basis = await run(panels.find(p => p.id === 134), [extracted('A', [performance])]);
  assert.deepEqual(field(basis[0], '마지막 관측'), [Date.parse(second)]);
  assert.deepEqual(field(basis[0], '기준 원금 KRW'), [1000]);
  console.log('PASS: fee-inclusive P&L fields, percent units, unknown nulls, lifetime basis/time');
  console.log('PASS: actual-time axes, nulls, latest-event dedup, distinct fills, tables, empty input');
  console.log('PASS: overview cards keep timestamps, newest row, unknown empty state, last worker event');
})().catch(error => { console.error(error); process.exitCode = 1; });
