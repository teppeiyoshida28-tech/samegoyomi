/* Source-image chart. The ocean forecast has an independent timeline and no point-score overlays. */
let mapController;
function renderIsland() {
  const root = document.getElementById('samegoyomi-current');
  if (!root) return;
  if (mapController) { mapController.refresh(); return; }
  try { mapController = createMapController(root); }
  catch (error) {
    root.querySelector('#sc-data-status').textContent = '地図データを表示できません';
    root.querySelector('#sc-caption').textContent = 'データの整合性を確認できないため、流れの表示を保留しています。';
    root.querySelector('#sc-play').disabled = true;
    console.error('Ocean map initialization failed', error);
  }
}
function createMapController(root) {
  const A = OceanCurrent, data = MAP_TRACE, flow = MAP_FLOW, forecast = OCEAN_CURRENT;
  if (!data?.features || data.source?.sha256 !== flow?.source?.traceSha256) throw new Error('Chart / field source mismatch');
  const fields = A.decodeFields(flow), box = flow.viewBox, bounds = flow.drawBounds;
  const svg = root.querySelector('#sc-svg'), canvas = root.querySelector('#sc-particles'), ctx = canvas.getContext('2d');
  const get = id => root.querySelector('#' + id), NS = 'http://www.w3.org/2000/svg';
  const node = (tag, attrs = {}) => { const e = document.createElementNS(NS, tag); Object.entries(attrs).forEach(([k,v]) => e.setAttribute(k,String(v))); return e; };
  const state = { mode:'schematic', depths:true, running:true, day:null, hour:8 };
  let prepared = A.prepare(forecast), current, destroyed = false, particles = [], raf = 0, resizeRaf = 0, last = 0, visible = true;
  let scale = 1, ratio = 1, offsetX = 0, offsetY = 0, width = 0, height = 0, seed = 21017, simTime = 0;
  let checkedDate = A.jstDate(Date.now()), checkedStatus = A.bundleStatus(forecast), checkedPast = false;
  const motion = window.matchMedia('(prefers-reduced-motion: reduce)'), cleanups = [];
  const hitCtx = document.createElement('canvas').getContext('2d'); hitCtx.lineWidth = 6;
  const obstacles = data.features.filter(f => f.kind === 'exposed').map(f => ({shape:new Path2D(f.path), bbox:f.bbox}));
  const appearance = () => A.strengthStyle(current?.kt,width);
  const rgb = (s,alpha) => `rgba(${s.rgb.join(',')},${alpha})`, waterAlpha = .36;
  const random = () => { seed = (Math.imul(seed,1664525)+1013904223)>>>0; return seed/4294967296; };
  const listen = (target, event, callback) => { target.addEventListener(event,callback); cleanups.push(() => target.removeEventListener(event,callback)); };
  function save() { try { sessionStorage.setItem('samegoyomi-ocean-v1',JSON.stringify(state)); } catch {} }
  function initialSelection() {
    state.day = prepared.days[0] || null; state.hour = 8;
    const now = Date.now();
    for (const day of prepared.days) for (const hour of A.HOURS) {
      const key = A.targetTime(day,hour);
      if (A.validRecord(prepared.records.get(key)) && A.timestamp(key) >= now) { state.day=day; state.hour=hour; return; }
    }
    if (state.day) state.hour = A.HOURS.find(hour => A.validRecord(prepared.records.get(A.targetTime(state.day,hour)))) || 8;
  }
  initialSelection();
  try {
    const saved = JSON.parse(sessionStorage.getItem('samegoyomi-ocean-v1') || 'null');
    if (saved) {
      if (['schematic','regional'].includes(saved.mode)) state.mode=saved.mode;
      if (typeof saved.depths==='boolean') state.depths=saved.depths;
      if (typeof saved.running==='boolean') state.running=saved.running;
      if (prepared.days.includes(saved.day) && A.HOURS.includes(saved.hour)) { state.day=saved.day; state.hour=saved.hour; }
    }
  } catch {}
  svg.setAttribute('viewBox',box.join(' '));
  for (const f of data.features) {
    const p=node('path',{d:f.path,class:f.kind,'data-source-feature':f.id});
    get(f.kind==='submerged'?'sc-reefs':f.kind==='exposed'?'sc-land':'sc-open').append(p);
    for (const [a,b] of f.occlusion_links || []) get('sc-review').append(node('path',{d:`M${a.join(',')} L${b.join(',')}`,fill:'none',stroke:'#f0c776','stroke-width':1.7,'stroke-dasharray':'2.5 3','vector-effect':'non-scaling-stroke'}));
  }
  get('sc-symbols').append(node('circle',{cx:504,cy:508,r:7,fill:'#f1d7a0',stroke:'#0a2336','stroke-width':2}));
  for (const t of [{text:'灯台',anchor:[530,508],id:'lighthouse'},{text:'神子元島',anchor:[430,420],id:'island'}]) {
    const e=node('text',{x:t.anchor[0],y:t.anchor[1],'dominant-baseline':'central','text-anchor':t.id==='island'?'middle':'start','data-symbol':t.id}); e.textContent=t.text; get('sc-symbols').append(e);
  }
  for (const label of data.labels) {
    const e=node('text',{x:label.anchor[0],y:label.anchor[1],'text-anchor':'middle','dominant-baseline':'central','data-related':label.related_id});e.textContent=label.text;get('sc-names').append(e);
  }
  for (const depth of data.depths) {
    const e=node('text',{x:depth.anchor[0],y:depth.anchor[1],'text-anchor':'middle','dominant-baseline':'central',class:'depth','data-depth':depth.id});e.textContent=depth.text;get('sc-depths').append(e);
  }
  function onLand(x,y) {
    for (const o of obstacles) {const b=o.bbox;if(x<b[0]-4||y<b[1]-4||x>b[0]+b[2]+4||y>b[1]+b[3]+4)continue;if(hitCtx.isPointInPath(o.shape,x,y)||hitCtx.isPointInStroke(o.shape,x,y))return true;}return false;
  }
  function sample(x,y) {
    if (current?.status!=='available'||x<bounds[0]||y<bounds[1]||x>bounds[0]+bounds[2]||y>bounds[1]+bounds[3]||onLand(x,y)) return null;
    return state.mode==='regional' ? A.vector(current.direction) : A.sampleField(flow,fields,x,y,current.direction);
  }
  function spawn(p,initial=false) {
    for(let i=0;i<90;i++){const x=bounds[0]+random()*bounds[2],y=bounds[1]+2+random()*(bounds[3]-4);if(sample(x,y)){p.x=x;p.y=y;p.life=5+random()*7;p.age=initial?random()*p.life:0;p.tone=random();p.trail=[];return true;}}p.age=100;p.life=0;p.trail=[];return false;
  }
  function clear(){ctx.setTransform(1,0,0,1,0,0);ctx.clearRect(0,0,canvas.width,canvas.height);}
  function advance(dt) {
    if(!width||!height)return;
    simTime+=dt;clear();ctx.globalCompositeOperation='source-over';ctx.setTransform(scale*ratio,0,0,scale*ratio,offsetX*ratio,offsetY*ratio);
    ctx.save();ctx.beginPath();ctx.rect(...bounds);ctx.clip();ctx.lineCap='round';
    const style=appearance();ctx.fillStyle=rgb(style,style.valid?waterAlpha:.1);ctx.fillRect(...bounds);
    const buckets=Array.from({length:8},()=>new Path2D()),heads=new Path2D();
    const travel=style.speed*dt/scale,steps=Math.max(1,Math.ceil(travel/1.8)),ds=travel/steps;
    for(const p of particles){
      if(p.age>=p.life){spawn(p);continue;}p.age+=dt;
      const alpha=Math.max(0,Math.min(1,p.age/.45,(p.life-p.age)/.65));if(alpha===0)continue;
      if(!p.trail.length)p.trail.push({x:p.x,y:p.y,t:simTime-dt});let blocked=false;
      for(let s=0;s<steps;s++){
        const v=sample(p.x,p.y);if(!v){blocked=true;break;}
        const mx=p.x+v[0]*ds/2,my=p.y+v[1]*ds/2,mid=sample(mx,my);if(!mid){blocked=true;break;}
        const nx=p.x+mid[0]*ds,ny=p.y+mid[1]*ds;
        if(!sample(nx,ny)||onLand((p.x+nx)/2,(p.y+ny)/2)){blocked=true;break;}
        p.x=nx;p.y=ny;p.trail.push({x:nx,y:ny,t:simTime-dt+(s+1)*dt/steps});
      }
      while(p.trail.length>1&&simTime-p.trail[0].t>style.time)p.trail.shift();
      for(let j=1;j<p.trail.length;j++){const a=p.trail[j-1],b=p.trail[j],fade=Math.exp(-(simTime-b.t)/(style.time/2))*alpha*(.8+p.tone*.2),bucket=Math.min(7,Math.floor(fade*8));if(bucket===0)continue;buckets[bucket].moveTo(a.x,a.y);buckets[bucket].lineTo(b.x,b.y);}
      if(!blocked&&alpha>.65&&p.tone>.76&&p.trail.length>2){const v=sample(p.x,p.y);if(v){const arm=2.6/scale,wing=1.35/scale;heads.moveTo(p.x-v[0]*arm-v[1]*wing,p.y-v[1]*arm+v[0]*wing);heads.lineTo(p.x,p.y);heads.lineTo(p.x-v[0]*arm+v[1]*wing,p.y-v[1]*arm-v[0]*wing);}}
      if(blocked)p.age=p.life;
    }
    for(let b=1;b<8;b++){ctx.lineWidth=1.9/scale;ctx.strokeStyle=rgb(style,b/8*.9);ctx.stroke(buckets[b]);ctx.lineWidth=.85/scale;ctx.strokeStyle=`rgba(238,248,249,${b/8*.86})`;ctx.stroke(buckets[b]);}
    ctx.lineWidth=.85/scale;ctx.strokeStyle='rgba(238,248,249,.74)';ctx.stroke(heads);
    // Remove the complete tails and antialiased caps, not only particle heads.
    ctx.globalCompositeOperation='destination-out';ctx.fillStyle='#000';ctx.strokeStyle='#000';ctx.lineWidth=6;
    for(const o of obstacles){ctx.fill(o.shape);ctx.stroke(o.shape);}ctx.restore();ctx.globalCompositeOperation='source-over';
  }
  function resetParticles() {
    clear();seed=21017;simTime=0;const style=appearance(),count=style.moving?Math.round(Math.max(170,Math.min(760,bounds[2]*bounds[3]*scale*scale/650))):0;
    particles=Array.from({length:count},()=>{const p={};spawn(p,true);return p;});
    canvas.dataset.particleCount=String(count);canvas.dataset.renderer='continuous-field-rk2';canvas.dataset.tailLength=style.length.toFixed(2);canvas.dataset.strength=style.valid?String(style.kt):'missing';canvas.dataset.color=style.rgb.join(',');canvas.dataset.status=current.status;
    if(!count){advance(0);return;}for(let i=0;i<30;i++)advance(1/30);
  }
  function strengthLegend() {
    const style=appearance();get('sc-strength-gradient').style.background='linear-gradient(to right,'+A.COLOR_STOPS.map(([k])=>rgb(A.strengthStyle(k,width),waterAlpha)+' '+(k/A.MAX_KT*100)+'%').join(',')+'),#0a2336';
    get('sc-strength-marker').hidden=!style.valid;get('sc-strength-marker').style.left=Math.min(100,(style.kt||0)/A.MAX_KT*100)+'%';
    for(const c of root.querySelectorAll('[data-tail-example]')){
      const s=A.strengthStyle(Number(c.dataset.tailExample),width),p=c.getContext('2d'),d=Math.min(window.devicePixelRatio||1,2),w=c.getBoundingClientRect().width||90;
      c.width=Math.round(w*d);c.height=18*d;p.scale(d,d);const end=w-5,x=end-s.length;
      const fade=p.createLinearGradient(x,0,end,0);fade.addColorStop(0,rgb(s,.12));fade.addColorStop(1,rgb(s,1));p.strokeStyle=fade;p.lineWidth=1.9;p.lineCap='round';p.beginPath();p.moveTo(x,9);p.lineTo(end,9);p.stroke();p.strokeStyle='rgba(238,248,249,.8)';p.lineWidth=.85;p.beginPath();p.moveTo(end-2.6,7.65);p.lineTo(end,9);p.lineTo(end-2.6,10.35);p.stroke();
    }
  }
  function labelLayout() {
    const small=width<500,primary=['Aポイント','ザブ西','ザブ根','カメ根','プレート','ハンマーズロック','三つ目の高根'],keyDepth=['a_near','zabu_west','north_rock','plate','hammers','eagleray','third_high15','third_high18'];
    const texts=[...svg.querySelectorAll('text')],placed=[],priority=e=>e.dataset.symbol?0:e.classList.contains('depth')?2:primary.includes(e.textContent)?1:3;
    texts.sort((a,b)=>priority(a)-priority(b)).forEach(e=>{
      e.style.display='';e.style.fontSize=(e.dataset.symbol==='island'?14:12)/scale+'px';e.style.strokeWidth=3.8/scale+'px';
      const isDepth=e.classList.contains('depth'),allowed=(!isDepth||state.depths)&&(!small||e.dataset.symbol||(isDepth?keyDepth.includes(e.dataset.depth):primary.includes(e.textContent)));
      const q=e.getBBox(),pad=2/scale,r={x:q.x-pad,y:q.y-pad,w:q.width+2*pad,h:q.height+2*pad};
      const inside=r.x>=box[0]+3/scale&&r.x+r.w<=box[0]+box[2]-3/scale&&r.y>=box[1]+42/scale&&r.y+r.h<=box[1]+box[3]-3/scale;
      if(!allowed||!inside||placed.some(p=>r.x<p.x+p.w&&r.x+r.w>p.x&&r.y<p.y+p.h&&r.y+r.h>p.y))e.style.display='none';else placed.push(r);
    });
  }
  function layout() {
    if(destroyed)return;const w=svg.getBoundingClientRect().width;if(!w)return;
    const h=Math.min(w<500?600:820,w*box[3]/box[2]),dpr=Math.min(window.devicePixelRatio||1,2);svg.style.height=h+'px';
    const changed=Math.abs(width-w)>.5||Math.abs(height-h)>.5||ratio!==dpr;
    width=w;height=h;ratio=dpr;scale=Math.min(w/box[2],h/box[3]);offsetX=(w-box[2]*scale)/2-box[0]*scale;offsetY=(h-box[3]*scale)/2-box[1]*scale;
    if(changed){canvas.width=Math.round(w*ratio);canvas.height=Math.round(h*ratio);canvas.style.width=w+'px';canvas.style.height=h+'px';resetParticles();last=0;}
    labelLayout();strengthLegend();
  }
  function playing(){return !destroyed&&appearance().moving&&state.running&&visible&&!document.hidden&&!motion.matches&&root.isConnected;}
  function syncPlayback() {
    const active=playing(),moving=appearance().moving,requested=state.running&&!motion.matches&&moving;
    get('sc-play').disabled=motion.matches||!moving;get('sc-play').textContent=motion.matches||!moving?'静止表示':requested?'一時停止':'再生';get('sc-play').setAttribute('aria-pressed',String(requested));canvas.dataset.playing=String(active);
    cancelAnimationFrame(raf);raf=0;last=0;if(active)raf=requestAnimationFrame(frame);
  }
  function frame(t) {
    if(A.bundleStatus(forecast)!==checkedStatus){refresh();return;}
    if(!playing()){syncPlayback();return;}const dt=last?Math.min((t-last)/1000,.05):1/60;last=t;advance(dt);raf=requestAnimationFrame(frame);
  }
  function formatTime(value) {
    const time=A.timestamp(value);return Number.isFinite(time)?new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).format(time)+' JST':'不明';
  }
  function timeControls() {
    const focused=document.activeElement,focusDay=focused?.dataset.oceanDay,focusHour=focused?.dataset.oceanHour;
    get('ocean-day-tabs').replaceChildren();get('ocean-hour-controls').replaceChildren();
    for(const day of prepared.days){
      const count=A.HOURS.filter(hour=>A.validRecord(prepared.records.get(A.targetTime(day,hour)))).length;
      const b=document.createElement('button');b.type='button';b.dataset.oceanDay=day;b.setAttribute('aria-pressed',String(day===state.day));
      const date=new Date(A.timestamp(`${day}T12:00:00+09:00`));b.textContent=new Intl.DateTimeFormat('ja-JP',{timeZone:'Asia/Tokyo',month:'numeric',day:'numeric',weekday:'short'}).format(date);
      if(count<7){const small=document.createElement('small');small.textContent=`有効 ${count}/7時刻`;b.append(document.createElement('br'),small);}b.setAttribute('aria-label',`${day}、有効${count}/7時刻`);get('ocean-day-tabs').append(b);
    }
    for(const hour of state.day?A.HOURS:[]){
      const reading=A.select(forecast,prepared,state.day,hour),b=document.createElement('button');b.type='button';b.dataset.oceanHour=String(hour);b.setAttribute('aria-pressed',String(hour===state.hour));b.textContent=hour+':00';
      if(reading.status!=='available'){const small=document.createElement('small');small.textContent=reading.status==='expired'?'期限切れ':'欠測';b.append(small);}get('ocean-hour-controls').append(b);
    }
    if(focusDay)get('ocean-day-tabs').querySelector(`[data-ocean-day="${focusDay}"]`)?.focus({preventScroll:true});
    if(focusHour)get('ocean-hour-controls').querySelector(`[data-ocean-hour="${focusHour}"]`)?.focus({preventScroll:true});
  }
  function sourceLabels() {
    const source=forecast?.source||{},label=get('sc-source-label');label.replaceChildren();
    const provider=typeof source.provider==='string'?source.provider:'出典未取得',model=typeof source.model==='string'?source.model:'モデル不明';
    const link=document.createElement('a');let validUrl=false;try{validUrl=new URL(source.source_url).protocol==='https:';}catch{}
    if(validUrl){link.href=source.source_url;link.target='_blank';link.rel='noopener noreferrer';link.textContent=provider;label.append(link);}else label.append(provider);
    const resolution=typeof source.resolution_km==='number'?`約${source.resolution_km} km格子`:'格子解像度不明';
    label.append(` / ${model} · 海面流 · ${resolution}`);
    const rows=[['予測対象',current.time?formatTime(current.time):'対象時刻なし'],['データ取得',formatTime(forecast?.retrieved_at)],['モデル発表',Number.isFinite(A.timestamp(source.model_run_at))?formatTime(source.model_run_at):'不明（APIから未提供）'],['表示の更新期限',formatTime(forecast?.expires_at)]];
    const coords=value=>value&&Number.isFinite(value.latitude)&&Number.isFinite(value.longitude)?`${value.latitude.toFixed(4)}, ${value.longitude.toFixed(4)}`:null;
    if(coords(source.grid_coordinates))rows.push(['予報格子の座標',coords(source.grid_coordinates)]);
    const dl=get('sc-source-times');dl.replaceChildren();for(const [term,value] of rows){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=term;dd.textContent=value;dl.append(dt,dd);}
  }
  function render() {
    if(destroyed)return;current=A.select(forecast,prepared,state.day,state.hour);checkedPast=current.isPast;
    const regional=state.mode==='regional',style=appearance(),missing=current.status!=='available';
    root.style.setProperty('--flow-color',rgb(style,1));root.dataset.currentStatus=current.status;
    root.querySelectorAll('[data-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.mode===state.mode)));get('sc-depth-toggle').checked=state.depths;
    const direction=style.moving?`${['北','北東','東','南東','南','南西','西','北西'][Math.round(current.direction/45)%8]}へ ${Math.round(current.direction)}°`:'方向の描画なし';
    const value=style.valid?style.kt.toFixed(2)+' kt':current.status==='expired'?'期限切れ':'未取得';
    get('sc-direction').textContent=direction;get('sc-speed').textContent=value;get('sc-strength-value').textContent=value;
    get('sc-data-status').textContent=missing?(current.status==='expired'?'更新期限を過ぎたため表示を保留':'選択時刻の海流データがありません'):'広域の海面流予報';get('sc-data-status').dataset.state=current.status;
    get('sc-target-time').textContent=current.time?formatTime(current.time)+(current.isPast?' · 対象時刻を過ぎた予報':''):'現在選択できる予報時刻がありません';
    get('sc-explanation').textContent=missing?'欠測・期限切れは0 ktと区別します':'流速は南側全体に共通の一つの値です';
    get('sc-strength-status').textContent=missing?'流れの表示を保留しています':style.kt<.2?'0.2 kt未満：方向の描画を省略':style.kt>6?'6 kt以上：色と線の長さは6 ktを上限に表示':'色と線の長さが広域予報の流速に連動';
    get('sc-description').textContent='提供図をトレースした神子元島と南側の根。'+(current.time?formatTime(current.time)+'。':'')+value+'。'+(style.moving?direction+(regional?'への一様な流れ。':'へ島沿いに曲がる模式的な流れ。'):'方向は描画しません。')+'地点・深度ごとの流れの予報ではありません。';
    get('sc-mode-badge').textContent=(regional?'広域予報の向き':'回り込み · 模式')+' / '+value;get('sc-flow-legend').textContent=regional?'広域予報の向き':'島沿いの回り込み（模式）';
    get('sc-caption').textContent=regional?'海面の広域予報を一様に表示しています。根ごとの流速差・水中の流れは表していません。':'広域予報の向きと流速を入力に、島沿いの曲がりを模式表示しています。地点・深度ごとの流れを予報するものではありません。';
    timeControls();sourceLabels();layout();resetParticles();syncPlayback();
  }
  function refresh() {
    if(destroyed)return;if(!root.isConnected){destroy();return;}
    const date=A.jstDate(Date.now()),status=A.bundleStatus(forecast),past=current?.time?A.timestamp(current.time)<Date.now():false;
    if(date!==checkedDate){checkedDate=date;prepared=A.prepare(forecast);if(!prepared.days.includes(state.day))initialSelection();checkedStatus=status;render();}
    else if(status!==checkedStatus||past!==checkedPast){checkedStatus=status;render();}
    else syncPlayback();
  }
  listen(get('ocean-day-tabs'),'click',event=>{const b=event.target.closest('[data-ocean-day]');if(!b)return;state.day=b.dataset.oceanDay;render();save();});
  listen(get('ocean-hour-controls'),'click',event=>{const b=event.target.closest('[data-ocean-hour]');if(!b)return;state.hour=Number(b.dataset.oceanHour);render();save();});
  for(const button of root.querySelectorAll('[data-mode]'))listen(button,'click',()=>{state.mode=button.dataset.mode;render();save();});
  listen(get('sc-depth-toggle'),'change',event=>{state.depths=event.target.checked;labelLayout();save();});
  listen(get('sc-play'),'click',()=>{state.running=!state.running;syncPlayback();save();});
  listen(document,'visibilitychange',refresh);listen(motion,'change',refresh);listen(window,'pageshow',refresh);
  const intersection=typeof IntersectionObserver==='function'?new IntersectionObserver(entries=>{visible=entries[0].isIntersecting;refresh();},{threshold:0}):null;intersection?.observe(svg);
  const resize=new ResizeObserver(()=>{if(resizeRaf||destroyed)return;resizeRaf=requestAnimationFrame(()=>{resizeRaf=0;refresh();layout();});});resize.observe(svg);
  const timer=setInterval(refresh,30000);
  const untilExpiry=A.timestamp(forecast?.expires_at)-Date.now();
  const expiryTimer=Number.isFinite(untilExpiry)&&untilExpiry>0?setTimeout(refresh,Math.min(untilExpiry+1,2147483647)):null;
  function destroy(){destroyed=true;cancelAnimationFrame(raf);cancelAnimationFrame(resizeRaf);clearInterval(timer);clearTimeout(expiryTimer);intersection?.disconnect();resize.disconnect();cleanups.forEach(remove=>remove());clear();}
  render();
  return { refresh, destroy };
}
