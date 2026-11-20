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
test('complete long calendar has independent freshness even with unknown or expired Kuroshio',()=>{
  const now=Date.parse('2026-11-21T10:00:00+09:00');
  const days=Array.from({length:183},(_,i)=>({date:gate.jstDate(now+i*86400000)}));
  const base={schema_version:2,generated_at:'2026-11-21T06:30:00+09:00',days,coverage:{start:days[0].date,end:days.at(-1).date,days:183,complete:true},kuroshio:{status:'unknown',score:null,valid_until:'2026-11-23'}};
  assert.equal(gate.longrange(base,now),true);
  assert.equal(gate.longrange({...base,kuroshio:{status:'unknown',valid_until:'2026-10-17'}},now),true);
  assert.equal(gate.forecast({generated_at:'2026-10-04T00:00:00+09:00',daily:[{date:'2026-10-04'}]},now),false);
  assert.equal(gate.longrange(base,now),true,'short forecast staleness is independent');
  for(const change of [
    {...base,days:days.slice(1)},
    {...base,days:[...days.slice(0,50),days[48],...days.slice(51)]},
    {...base,coverage:{...base.coverage,complete:false}},
    {...base,coverage:{...base.coverage,end:'2027-05-23'}},
    {...base,generated_at:'2026-11-19T06:30:00+09:00'},
    {...base,generated_at:'2026-11-22T06:30:00+09:00'},
    {...base,schema_version:1},
  ])assert.equal(gate.longrange(change,now),false);
  assert.equal(gate.longrange(base,Date.parse('2026-11-22T00:00:00+09:00')),true,'an intact recent calendar remains useful after midnight');
});
