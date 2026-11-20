const {test}=require('node:test');
const assert=require('node:assert/strict');
const gate=require('../engine/forecast_freshness.js');
test('old and future generations cannot present themselves as current forecasts',()=>{
  const now=Date.parse('2026-11-21T10:00:00+09:00');
  const base={generated_at:'2026-11-21T06:30:00+09:00',daily:[{date:'2026-11-21'}]};
  assert.equal(gate.forecast(base,now),true);
  for(const generated_at of ['2026-10-16T08:44:00+09:00','2026-11-22T06:30:00+09:00','2026-11-21T06:30:00',null])assert.equal(gate.forecast({...base,generated_at},now),false);
  assert.equal(gate.forecast(base,Date.parse('2026-11-22T00:00:00+09:00')),false);
});
test('long calendar is withheld when its Kuroshio assumption expires',()=>{
  const now=Date.parse('2026-11-21T10:00:00+09:00');
  const base={generated_at:'2026-11-21T06:30:00+09:00',days:[{date:'2026-11-21'}],kuroshio:{valid_until:'2026-11-23'}};
  assert.equal(gate.longrange(base,now),true);
  assert.equal(gate.longrange({...base,kuroshio:{valid_until:'2026-10-17'}},now),false);
});
