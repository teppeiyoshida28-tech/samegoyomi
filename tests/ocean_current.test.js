const assert = require('node:assert/strict');
const test = require('node:test');
const O = require('../engine/ocean_current.js');

const now = Date.parse('2026-11-21T07:00:00+09:00');
function fixture() {
  const hourly = [];
  for (const day of ['2026-11-21', '2026-11-22']) for (const hour of O.HOURS) hourly.push({
    time: O.targetTime(day, hour), current_velocity_kmh: 1.852, current_direction: 90, status: 'available'
  });
  return {
    schema_version: 1, retrieved_at: '2026-11-21T06:30:00+09:00', expires_at: '2026-11-22T18:30:00+09:00', status: 'available',
    source: { velocity_unit: 'km/h', direction_convention: 'towards', model_run_at: null },
    hourly, coverage: { start: hourly[0].time, end: hourly.at(-1).time, last_valid_time: hourly.at(-1).time }
  };
}
const reading = (bundle, day='2026-11-21', hour=8, time=now) => O.select(bundle,O.prepare(bundle,time),day,hour,time);

test('JST timeline selects exact records, exposes holes and excludes days without any valid diving hour', () => {
  const f=fixture();
  // API records with seconds omitted and UTC timestamps still address the same instant.
  f.hourly[0].time='2026-11-20T23:00Z';
  f.hourly[1].time='2026-11-21T09:00+09:00';
  assert.deepEqual(O.prepare(f,now).days,['2026-11-21','2026-11-22']);
  assert.equal(reading(f).kt,1);
  f.hourly[1].status='missing';f.hourly[1].current_velocity_kmh=null;
  assert.equal(reading(f,'2026-11-21',9).status,'missing');
  assert.equal(reading(f,'2026-11-21',9).kt,null);
  assert.equal(reading(f,'2026-11-21',10).kt,1);
  for(const record of f.hourly.slice(7))record.status='missing';
  assert.deepEqual(O.prepare(f,now).days,['2026-11-21']);
  assert.deepEqual(O.prepare(f,Date.parse('2026-11-22T00:00+09:00')).days,[]);
  assert.equal(reading(f,'2026-11-21',8,Date.parse('2026-11-21T08:01+09:00')).isPast,true);
  assert.equal(O.timestamp('2026-11-21T08:00'),NaN,'naive local dates must not be host-timezone dependent');
  f.hourly.push({...f.hourly[0]});
  assert.equal(reading(f).status,'missing','ambiguous duplicate timestamps are not silently selected');
});

test('missing, malformed, expired and calm data remain distinct, including expiry while paused', () => {
  assert.equal(reading(fixture()).status,'available');
  const partial=fixture();partial.status='partial';assert.equal(reading(partial).status,'available');
  for(const value of [null,undefined,NaN,Infinity,-1,'2']) {
    const f=fixture();f.hourly[0].current_velocity_kmh=value;
    assert.equal(reading(f).kt,null);assert.equal(reading(f).status,'missing');
  }
  for(const value of [null,undefined,NaN,Infinity,-1,361,'90']) {
    const f=fixture();f.hourly[0].current_direction=value;
    assert.equal(reading(f).direction,null);assert.equal(reading(f).status,'missing');
  }
  const calm=fixture();calm.hourly[0].current_velocity_kmh=0;
  assert.equal(reading(calm).status,'available');assert.equal(reading(calm).kt,0);
  for(const property of ['retrieved_at','expires_at']) {
    const f=fixture();f[property]=null;assert.equal(reading(f).status,'missing');
  }
  const f=fixture(),expires=Date.parse(f.expires_at);
  assert.equal(reading(f,'2026-11-21',8,expires-1).status,'available');
  assert.equal(reading(f,'2026-11-21',8,expires).status,'expired');
  assert.equal(reading(f,'2026-11-21',8,expires).kt,null);
  f.source.velocity_unit='kn';assert.equal(reading(f).status,'missing');
  f.source.velocity_unit='km/h';f.source.direction_convention='from';assert.equal(reading(f).status,'missing');
});

test('one uncapped regional value drives a common 0–6 kt scale and a raw 0.2 kt rendering threshold', () => {
  const f=fixture();f.hourly[0].current_velocity_kmh=1.852*7;
  assert.equal(reading(f).kt,7);
  for(const width of [320,390,600]) {
    const scale=[0,.19,.2,1,3,6].map(k=>O.strengthStyle(k,width));
    assert.equal(scale[0].valid,true);assert.equal(scale[0].moving,false);
    assert.equal(scale[1].moving,false);assert.equal(scale[1].length,0);
    assert.equal(scale[2].moving,true);
    assert.ok(scale[2].length<scale[3].length&&scale[3].length<scale[4].length&&scale[4].length<scale[5].length);
    const high=O.strengthStyle(7,width),max=O.strengthStyle(6,width);
    assert.equal(high.kt,7);assert.equal(high.cappedKt,6);assert.deepEqual(high.rgb,max.rgb);assert.equal(high.length,max.length);
    for(const s of scale)assert.ok(s.rgb.every(c=>Number.isInteger(c)&&c>=0&&c<=255));
  }
  assert.equal(O.strengthStyle(null).valid,false);
  assert.notDeepEqual(O.strengthStyle(null).rgb,O.strengthStyle(0).rgb);
});

function basis() {
  const s=Math.SQRT1_2;
  return {
    flow:{schemaVersion:2,grid:{origin:[0,0],spacing:1,nx:2,ny:2},drawBounds:[0,0,1,1],basis:{normalizedAtNodes:false},rendering:{minimumCombinedNorm:.025}},
    fields:{ne:Float32Array.from([s,-s,s,-s,s,-s,s,-s]),west:Float32Array.from([-1,0,-1,0,-1,0,-1,0])}
  };
}
test('arbitrary toward bearings blend unnormalized bases before one final normalization', () => {
  const {flow,fields}=basis();
  for(let deg=0;deg<360;deg+=7) {
    const actual=O.sampleField(flow,fields,.5,.5,deg),expected=O.vector(deg);
    assert.ok(Math.hypot(actual[0]-expected[0],actual[1]-expected[1])<1e-6);
  }
  for(const [deg,wanted] of [[0,[0,-1]],[90,[1,0]],[180,[0,1]],[270,[-1,0]],[45,[Math.SQRT1_2,-Math.SQRT1_2]],[225,[-Math.SQRT1_2,Math.SQRT1_2]]]) {
    const actual=O.sampleField(flow,fields,.2,.7,deg);assert.ok(Math.hypot(actual[0]-wanted[0],actual[1]-wanted[1])<1e-6);
  }
  const before=O.sampleField(flow,fields,.2,.7,359.999),after=O.sampleField(flow,fields,.2,.7,.001);
  assert.ok(Math.hypot(before[0]-after[0],before[1]-after[1])<.0001);
  assert.equal(O.sampleField(flow,fields,.5,-.01,90),null);
  fields.ne.fill(0);fields.west.fill(0);assert.equal(O.sampleField(flow,fields,.5,.5,120),null);
  // A weak basis may combine into a usable field. Reject only the combined norm.
  fields.ne.set([.02,0,.02,0,.02,0,.02,0]);fields.west.set([0,.02,0,.02,0,.02,0,.02]);
  assert.ok(O.sampleField(flow,fields,.5,.5,0));
});

test('published field decodes only the approved raw basis and preserves trace linkage', () => {
  const field=require('../engine/map_flow_field.json'),trace=require('../engine/map_trace.json');
  assert.equal(field.source.traceSha256,trace.source.sha256);
  assert.equal(field.basis.normalizedAtNodes,false);
  const fields=O.decodeFields(field),count=field.grid.nx*field.grid.ny*2;
  assert.equal(fields.ne.length,count);assert.equal(fields.west.length,count);
  assert.throws(()=>O.decodeFields({...field,schemaVersion:1}));
  assert.throws(()=>O.decodeFields({...field,basis:{normalizedAtNodes:true}}));
  assert.throws(()=>O.decodeFields({...field,fields:{...field.fields,ne:{...field.fields.ne,data:'AAAA'}}}));
  for(const degree of [0,45,90,180,225,270,359]) {
    const v=O.sampleField(field,fields,900,1300,degree);
    assert.ok(v&&v.every(Number.isFinite));assert.ok(Math.abs(Math.hypot(...v)-1)<1e-6);
  }
});
