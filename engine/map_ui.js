/* Schematic forecast map. The source registry is shared with Python scoring. */
let mapController;
function renderIsland(data) {
  renderTodayReco(data);
  const hours = todayHourly(data), hour = hours[selectedHourIdx] || hours[0];
  if (!hour) return;
  if (!mapController) mapController = createMapController();
  mapController.update(hour, data.daily[selectedDayIdx]);
}

function createMapController() {
  const svg = $('#forecast-map'), canvas = $('#map-canvas');
  const points = Object.entries(POINTS);
  const baseBounds = MapGeometry.union([
    ...points.map(([,p]) => ({left:p.pos[0]-40,right:p.pos[0]+40,top:p.pos[1]-40,bottom:p.pos[1]+40})),
    ...MAP_CATALOG.island_outline_m.map(([x,y]) => ({left:x,right:x,top:y,bottom:y}))
  ]);
  let view, hour, day, selected = null, drag = null, suppressClick = false, flowMode = 'regional';
  const escape = s => String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const size = () => ({w:canvas.clientWidth || 320,h:canvas.clientHeight || 480});
  function fit(includeHypothesis = false) {
    const s = size();
    let bounds = baseBounds;
    if (includeHypothesis && hour) {
      const h = MapGeometry.hypothesis(MAP_CATALOG.shelf_radii_m, hour.current_direction, hour.current_velocity_kmh);
      bounds = MapGeometry.union([bounds,MapGeometry.ellipseBounds(h.zone),MapGeometry.ellipseBounds(h.lee)]);
    }
    view = MapGeometry.fit(bounds,s.w,s.h);
    draw();
  }
  function choose(key, center = false) {
    selected = key;
    $('#point-select').value = key;
    if (center) {
      const p = POINTS[key].pos;
      view = MapGeometry.zoom(view, Math.min(1,700/view.w));
      view.x = p[0]-view.w/2; view.y = p[1]-view.h/2;
    }
    draw();
  }
  function draw() {
    if (!hour || !view) return;
    const s = size(), unit = view.w / s.w;
    const scores = hour.point_scores;
    const ranked = Object.keys(scores).filter(k => POINTS[k]).sort((a,b)=>scores[b]-scores[a]);
    const top = ranked.slice(0,3);
    const h = MapGeometry.hypothesis(MAP_CATALOG.shelf_radii_m,hour.current_direction,hour.current_velocity_kmh);
    const hypothesisOn = $('#map-hypothesis').checked;
    const allNames = $('#map-labels').checked;
    const current = MapGeometry.currentVector(hour.current_direction,hour.current_velocity_kmh);
    const ellipse = (e,cls) => `<ellipse class="${cls}" cx="${e.x}" cy="${e.y}" rx="${e.rx}" ry="${e.ry}" transform="rotate(${e.rotation} ${e.x} ${e.y})"/>`;
    const text = (x,y,label,color='#f2ead8',font=12) => `<text x="${x}" y="${y}" text-anchor="middle" fill="${color}" font-size="${font*unit}" class="map-label">${escape(label)}</text>`;
    const arrows=MapGeometry.flowArrows(view,s.w,hour.current_direction,hour.current_velocity_kmh,MAP_CATALOG.island_outline_m,flowMode);
    let content='<g class="map-current-arrows '+(flowMode==='local'?'is-local':'')+'" aria-hidden="true">';
    for(const path of arrows) {
      const end=path[path.length-1], prev=path[path.length-2];
      const angle=Math.atan2(end[1]-prev[1],end[0]-prev[0]), head=6*unit;
      const left=[end[0]-head*Math.cos(angle-.5),end[1]-head*Math.sin(angle-.5)];
      const right=[end[0]-head*Math.cos(angle+.5),end[1]-head*Math.sin(angle+.5)];
      content+=`<path d="M ${path.map(p=>p.join(',')).join(' L ')}"/><path class="flow-arrow-head" d="M ${left.join(',')} L ${end.join(',')} L ${right.join(',')}"/>`;
    }
    content+='</g>';
    content += `<path class="map-island" d="M ${MAP_CATALOG.island_outline_m.map(p=>p.join(',')).join(' L ')} Z"/>`;
    content += text(0,15,'神子元島','#f2ead8',13);
    if (hypothesisOn) {
      content = ellipse(h.lee,'hypothesis-lee') + ellipse(h.zone,'hypothesis-zone') + content;
      content += text(h.zone.x,h.zone.y,'ハンマー予想域（仮説）','#8bd1bf',12);
      content += text(h.lee.x,h.lee.y,'潮陰（仮説）','#abc4d8',12);
    }
    const placed = [];
    const order = [...new Set([selected,...top,...ranked].filter(Boolean))];
    for (const key of order) {
      const p = POINTS[key], [x,y] = p.pos, rank = top.indexOf(key)+1;
      const visible = x>=view.x && x<=view.x+view.w && y>=view.y && y<=view.y+view.h;
      if (!visible) continue;
      const active = selected===key, radius = (active?9:rank?8:5)*unit;
      const color = MapGeometry.scoreColor(scores[key] || 0);
      const label = `${p.label} ${Math.round((scores[key]||0)*100)}`;
      content += `<g class="map-point" role="button" tabindex="0" data-point="${key}" aria-label="${escape(label)}" aria-pressed="${active}">`;
      content += `<title>${escape(label)}</title><circle cx="${x}" cy="${y}" r="${14*unit}" fill="transparent"/>`;
      content += `<circle cx="${x}" cy="${y}" r="${radius}" fill="${color}" stroke="${active?'#fff':'#071727'}" stroke-width="${2*unit}"/>`;
      if (rank) content += text(x,y+4*unit,rank,'#071727',11);
      content += '</g>';
      if (rank || active || allNames) {
        const labelText = p.label;
        const width = [...labelText].reduce((n,c)=>n+(c.charCodeAt(0)>255?12:7),0)*unit;
        const height = 16*unit;
        let target;
        for (const [dx,dy] of [[0,-21],[0,29],[width/2/unit+15,5],[-width/2/unit-15,5],[0,-43],[0,51]]) {
          const cx=x+dx*unit, cy=y+dy*unit;
          const box = {l:cx-width/2-3*unit,r:cx+width/2+3*unit,t:cy-height,b:cy+3*unit};
          if (box.l<view.x || box.r>view.x+view.w || box.t<view.y || box.b>view.y+view.h) continue;
          if (placed.some(b=>box.l<b.r&&box.r>b.l&&box.t<b.b&&box.b>b.t)) continue;
          placed.push(box); target=[cx,cy]; break;
        }
        if (target) {
          content += `<line x1="${x}" y1="${y}" x2="${target[0]}" y2="${target[1]-6*unit}" stroke="${color}" stroke-width="${unit}" opacity=".6"/>`;
          content += text(...target,labelText,active?'#fff':'#dfebec');
        }
      }
    }
    svg.setAttribute('viewBox',`${view.x} ${view.y} ${view.w} ${view.h}`);
    svg.innerHTML = content;
    const scale = MapGeometry.scaleBar(view.w,s.w);
    $('#map-scale-line').style.width = scale.pixels+'px';
    $('#map-scale-label').textContent = `概略 ${scale.metres} m`;
    const flowKey=$('#map-flow-key');
    flowKey.className='map-flow-key'+(flowMode==='local'?' is-local':'');
    flowKey.hidden=flowMode==='off';
    flowKey.textContent=!current?'海流データなし':current.kt<.2?'弱い流れ · 矢印を省略':flowMode==='local'?'回り込みの仮説':'広域の表面流';
    $('#map-flow-description').textContent=flowMode==='off'?'流れの矢印は非表示です。':!current?'選択時刻の海流データがありません。':
      current.kt<.2?`広域の表面流 ${current.kt.toFixed(2)} kt · 0.2 kt未満のため矢印を省略しています。`:
      flowMode==='local'?`金色の曲線は未検証の回り込みイメージです。入力の広域予報は${current.kt.toFixed(1)} kt・${hour.current_direction.toFixed(0)}°へ。地点ごとの流速予報ではありません。`:
      `青い矢印は${hour.hour}:00の広域予報：${current.kt.toFixed(1)} kt・${hour.current_direction.toFixed(0)}°へ流れる向き。同じ予報値を繰り返し描いています。長さは流速の目安です。`;
    $('#map-zone-status').textContent = !hypothesisOn ? '潮陰・予想域の仮説は非表示' :
      MapGeometry.contains(view,MapGeometry.ellipseBounds(h.zone)) ? '予想域は未検証の仮説です' : '予想域の一部または全体が画面外です。「予想域まで表示」で確認できます。';
    const currentLabel = `${hour.hour}:00の推奨`;
    $('#map-ranked').innerHTML = top.map((k,i)=>`<button type="button" data-select="${k}" aria-pressed="${k===selected}"><span class="map-rank">${i+1}</span><span>${escape(POINTS[k].label)}</span><b>${Math.round(scores[k]*100)}</b></button>`).join('');
    $('#map-ranked-title').textContent = currentLabel;
    const p = POINTS[selected];
    $('#map-point-detail').innerHTML = `<strong>${escape(p.label)}</strong><span>地点スコア ${Math.round(scores[selected]*100)} / 100</span><p>${escape(p.note || '地点の詳細は現地ガイドに確認してください。')}</p><p>参考水深 ${escape(p.depth==='-'?'不明':p.depth)} · 位置・形状は未校正</p>`;
  }
  $('#point-select').innerHTML = points.map(([key,p])=>`<option value="${key}">${escape(p.label)}</option>`).join('');
  $('#point-select').addEventListener('change',e=>choose(e.target.value,true));
  $('#map-ranked').addEventListener('click',e=>{const b=e.target.closest('[data-select]');if(b)choose(b.dataset.select,true);});
  svg.addEventListener('click',e=>{if(suppressClick){suppressClick=false;return;}const p=e.target.closest('[data-point]');if(p)choose(p.dataset.point);});
  svg.addEventListener('keydown',e=>{
    const p=e.target.closest('[data-point]');
    if(p && ['Enter',' '].includes(e.key)){e.preventDefault();choose(p.dataset.point);$('#point-select').focus();return;}
    const moves={ArrowLeft:[-.2,0],ArrowRight:[.2,0],ArrowUp:[0,-.2],ArrowDown:[0,.2]};
    if(moves[e.key]){e.preventDefault();view.x+=moves[e.key][0]*view.w;view.y+=moves[e.key][1]*view.h;draw();}
  });
  svg.addEventListener('pointerdown',e=>{
    if(e.button!==0 || drag)return;
    drag={id:e.pointerId,x:e.clientX,y:e.clientY,view:{...view},moved:false};
    suppressClick=false;
  });
  svg.addEventListener('pointermove',e=>{
    if(!drag||e.pointerId!==drag.id)return;
    const dx=e.clientX-drag.x,dy=e.clientY-drag.y;
    if(Math.hypot(dx,dy)>4 && !drag.moved){drag.moved=true;svg.setPointerCapture(e.pointerId);}
    if(drag.moved){const s=size();view={...drag.view,x:drag.view.x-dx*drag.view.w/s.w,y:drag.view.y-dy*drag.view.h/s.h};draw();}
  });
  function stopDrag(e){if(drag&&e.pointerId===drag.id){suppressClick=drag.moved;drag=null;}}
  svg.addEventListener('pointerup',stopDrag);svg.addEventListener('pointercancel',stopDrag);
  $('#map-zoom-in').onclick=()=>{view=MapGeometry.zoom(view,.65);draw();};
  $('#map-zoom-out').onclick=()=>{view=MapGeometry.zoom(view,1/.65);draw();};
  $('#map-reset').onclick=()=>fit();
  $('#map-fit-zone').onclick=()=>{$('#map-hypothesis').checked=true;fit(true);};
  $('#map-hypothesis').onchange=draw;$('#map-labels').onchange=draw;
  document.querySelectorAll('[data-flow-mode]').forEach(button=>button.addEventListener('click',()=>{
    flowMode=button.dataset.flowMode;
    document.querySelectorAll('[data-flow-mode]').forEach(b=>b.setAttribute('aria-pressed',String(b===button)));
    draw();
  }));
  $('#map-fullscreen').onclick=async()=>{
    try {if(document.fullscreenElement)await document.exitFullscreen();else await $('#map-workbench').requestFullscreen();}
    catch {$('#map-zone-status').textContent='このブラウザーは全画面表示に対応していません。＋で拡大できます。';}
  };
  document.addEventListener('fullscreenchange',()=>{
    $('#map-fullscreen').textContent=document.fullscreenElement?'全画面を終了':'全画面';
  });
  new ResizeObserver(()=>{if(view){const s=size(),h=view.w*s.h/s.w;view.y+=(view.h-h)/2;view.h=h;draw();}}).observe(canvas);
  return {update(h,d){
    const changed=hour?.time!==h.time;
    hour=h;day=d;
    const ranked=Object.keys(h.point_scores).filter(k=>POINTS[k]).sort((a,b)=>h.point_scores[b]-h.point_scores[a]);
    if(changed||!selected)selected=ranked[0];
    $('#point-select').value=selected;
    $('#map-conditions').innerHTML=`<div><span>選択中</span><b>${escape(h.time.slice(0,10))} ${h.hour}:00</b></div>`+
      `<div><span>広域海流予報</span><b><i class="flow-bearing" style="transform:rotate(${h.current_direction}deg)">↑</i> ${(h.current_velocity_kmh/1.852).toFixed(1)} kt</b><small>${h.current_direction.toFixed(0)}°へ流れる · 約8km格子</small></div>`+
      `<div><span>海面水温（補正後）</span><b>${h.sst.toFixed(1)}℃</b><small>平年偏差 ${h.sst_anomaly>0?'+':''}${h.sst_anomaly.toFixed(1)}℃</small></div>`;
    const danger=d.diveable===false;
    $('#map-sea-status').className='map-sea-status'+(danger?' is-danger':'');
    $('#map-sea-status').textContent=`${d.sea_status} · この日の最大 波${d.max_wave.toFixed(1)}m / 風${d.max_wind.toFixed(1)}m/s`;
    if(!view)fit();else draw();
  }};
}
