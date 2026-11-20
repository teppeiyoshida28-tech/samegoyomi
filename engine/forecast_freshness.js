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
    return Boolean(data&&recent(data.generated_at,now)&&Array.isArray(data.days)&&data.days.some(d=>d.date===jstDate(now))&&typeof data.kuroshio?.valid_until==='string'&&data.kuroshio.valid_until>jstDate(now));
  }
  return {jstDate,recent,forecast,longrange};
});
