/* Browser freshness gate: stale archived predictions are not today's forecast. */
(function(root,factory){if(typeof module==='object'&&module.exports)module.exports=factory();else root.ForecastFreshness=factory();})(typeof globalThis!=='undefined'?globalThis:this,function(){
  'use strict';
  const MAX_AGE_MS=36*60*60*1000;
  function jstDate(now){return new Date(now+9*60*60*1000).toISOString().slice(0,10);}
  function recent(value,now){
    if(typeof value!=='string'||!/(?:Z|[+-]\d{2}:\d{2})$/.test(value))return false;
    const t=Date.parse(value);return Number.isFinite(t)&&t<=now+5*60*1000&&now-t<MAX_AGE_MS;
  }
  function forecast(data,now=Date.now()){
    return Boolean(data&&recent(data.generated_at,now)&&Array.isArray(data.daily)&&data.daily[0]?.date===jstDate(now));
  }
  function longrange(data,now=Date.now()){
    // Astronomy is not made stale by missing short-range weather or an unassessed
    // Kuroshio path. The calendar's own generation and complete coverage are gates.
    if(!data||data.schema_version!==2||!recent(data.generated_at,now)||!Array.isArray(data.days)||data.days.length!==183)return false;
    const c=data.coverage;
    if(!c||c.complete!==true||c.days!==183||c.start!==data.days[0]?.date||c.end!==data.days[182]?.date)return false;
    if(!/^\d{4}-\d{2}-\d{2}$/.test(c.start))return false;
    const first=Date.parse(c.start+'T00:00:00+09:00');
    if(!Number.isFinite(first)||jstDate(first)!==c.start)return false;
    for(let i=0;i<183;i++)if(data.days[i]?.date!==jstDate(first+i*86400000))return false;
    const today=jstDate(now);
    return c.start<=today&&today<=c.end;
  }
  return {jstDate,recent,forecast,longrange};
});
