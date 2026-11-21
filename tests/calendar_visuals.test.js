const {test}=require('node:test');
const assert=require('node:assert/strict');
const visuals=require('../engine/calendar_visuals.js');
const hours=Array.from({length:11},(_,i)=>i+6);
const day={date:'2026-11-21',tide_curve:hours.map(h=>(h-10)/10)};

test('lunar phases distinguish waxing right from waning left, and missing age is not new moon',()=>{
  const month=visuals.SYNODIC_MONTH;
  assert.equal(visuals.moonPhase(0).illumination,0);
  assert.equal(visuals.moonPhase(month/2).illumination,1);
  const first=visuals.moonPhase(month/4), last=visuals.moonPhase(month*3/4);
  assert.ok(Math.abs(first.illumination-.5)<1e-10);
  assert.ok(Math.abs(last.illumination-.5)<1e-10);
  assert.equal(first.waxing,true);
  assert.equal(last.waxing,false);
  assert.match(visuals.moonIcon(month/4),/A10 10 0 0 1 12 22/);
  assert.match(visuals.moonIcon(month*3/4),/A10 10 0 0 0 12 22/);
  for(const value of [null,undefined,NaN,'0',-1,30]) assert.equal(visuals.moonPhase(value),null);
  assert.equal(visuals.moonLabel(null),'月齢データなし');
});

test('tide window is exactly eight source values from 08:00 through 15:00',()=>{
  const points=visuals.tideSeries(day,hours);
  assert.deepEqual(points.map(p=>p.hour),[8,9,10,11,12,13,14,15]);
  assert.equal(points[0].level,day.tide_curve[2]);
  assert.equal(points.at(-1).level,day.tide_curve[9]);
  assert.equal(points[2].level,0,'zero is a real sea level value');
  const missing={...day,tide_curve:day.tide_curve.map((v,i)=>i===9?null:v)};
  assert.equal(visuals.tideSeries(missing,hours),null,'a missing 15:00 sample is not silently dropped');
  assert.equal(visuals.tideSeries(day,[6,7,8,9,10,11,12,13,14,14,16]),null);
  assert.equal(visuals.tideSeries(day,hours.slice(1)),null);
  assert.match(visuals.tidePlot(missing,hours,[-1,1],true),/潮位なし/);
  assert.doesNotMatch(visuals.tidePlot(missing,hours,[-1,1],true),/<path/);
});

test('shared height scale preserves differences between days and ignores hours outside the window',()=>{
  const small={tide_curve:hours.map(()=>.1)};
  const large={tide_curve:hours.map(h=>h===6||h===16?100:(h-10)/5)};
  const scale=visuals.tideScale([small,large],hours);
  assert.ok(scale[0]<=-.4&&scale[1]>=1);
  assert.ok(scale[1]<100);
  assert.ok(scale[1]-scale[0]>=1);
  assert.deepEqual(visuals.tideScale([]),[-1,1]);
});

test('smooth tide interpolation passes through all samples without inventing extrema',()=>{
  const points=[0,1,.8,.4,.4,1,2,1].map((y,x)=>({x,y}));
  const path=visuals.curvePath(points);
  const segments=[...path.matchAll(/C(-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+) (-?[\d.]+)/g)].map(m=>m.slice(1).map(Number));
  assert.equal(segments.length,points.length-1);
  segments.forEach((s,i)=>{
    assert.equal(s[4],points[i+1].x);
    assert.equal(s[5],points[i+1].y);
    for(let n=0;n<=100;n++){
      const t=n/100, u=1-t;
      const y=u*u*u*points[i].y+3*u*u*t*s[1]+3*u*t*t*s[3]+t*t*t*s[5];
      assert.ok(y>=Math.min(points[i].y,points[i+1].y)-1e-8&&y<=Math.max(points[i].y,points[i+1].y)+1e-8);
    }
  });
});

test('detailed tide plot labels hours and meters while preserving approximation and current distinction',()=>{
  const html=visuals.tidePlot(day,hours,[-1,1],false,310);
  assert.match(html,/viewBox="0 0 310 222"/);
  for(const hour of [8,9,10,11,12,13,14,15]) assert.ok(html.includes(`>${hour}時</text>`));
  assert.ok(!html.includes('>6時</text>')&&!html.includes('>16時</text>'));
  assert.match(html,/平均海面（MSL）基準の調和近似/);
  assert.match(html,/現地の流速・潮止まり時刻を示すものではありません/);
});
