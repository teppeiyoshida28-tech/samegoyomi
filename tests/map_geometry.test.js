const assert = require('node:assert/strict');
const test = require('node:test');
const G = require('../engine/map_geometry.js');
const catalog = require('../engine/map_points.json');

test('offscreen hypothesis keeps its model position when fitting the view', () => {
  const h = G.hypothesis(catalog.shelf_radii_m,255,2.1);
  assert.ok(h.zone.x < -700, 'westward forecast must remain west of the old canvas');
  const original = structuredClone(h.zone), bounds = G.ellipseBounds(h.zone);
  assert.equal(G.contains({x:-450,y:-200,w:900,h:1100},bounds),false);
  for (const [w,height] of [[340,460],[900,640]]) {
    const view=G.fit(G.union([bounds,{left:-400,right:400,top:-200,bottom:900}]),w,height);
    assert.equal(G.contains(view,bounds),true);
    assert.ok(Math.abs(view.w/view.h-w/height)<1e-10);
    assert.deepEqual(h.zone,original);
  }
});
test('flow bearings follow the API toward-direction convention', () => {
  for(const [deg,x,y] of [[0,0,-1],[90,1,0],[180,0,1],[270,-1,0]]) {
    const h=G.hypothesis(catalog.shelf_radii_m,deg,2);
    assert.ok(Math.abs(h.ux-x)<1e-10);
    assert.ok(Math.abs(h.uy-y)<1e-10);
  }
});
test('scale bar reflects world distance at different zooms and display sizes', () => {
  for(const [world,pixels] of [[900,340],[1200,900],[200,340],[3000,900]]) {
    const b=G.scaleBar(world,pixels);
    assert.ok(Math.abs(b.pixels*world/pixels-b.metres)<1e-10);
    assert.ok(b.pixels<=pixels*.22);
  }
});
test('zoom preserves the map center and aspect ratio', () => {
  const before={x:-470,y:-210,w:940,h:1200};
  const after=G.zoom(before,.65);
  assert.equal(after.x+after.w/2,before.x+before.w/2);
  assert.equal(after.y+after.h/2,before.y+before.h/2);
  assert.ok(Math.abs(after.w/after.h-before.w/before.h)<1e-10);
});
test('score colors use a fixed zero-to-one scale', () => {
  assert.equal(G.scoreColor(0),'rgb(143,163,176)');
  assert.equal(G.scoreColor(.5),'rgb(111,174,158)');
  assert.equal(G.scoreColor(1),'rgb(201,165,78)');
  assert.equal(G.scoreColor(2),G.scoreColor(1));
  assert.notEqual(G.scoreColor(.45),G.scoreColor(1));
});

test('current arrows use toward bearings and knots without another tide component', () => {
  for(const [bearing,x,y] of [[0,0,-1],[90,1,0],[180,0,1],[270,-1,0]]) {
    const v=G.currentVector(bearing,1.852*2);
    assert.equal(v.kt,2);
    assert.ok(Math.abs(v.x-x)<1e-10 && Math.abs(v.y-y)<1e-10);
    const paths=G.flowArrows({x:400,y:400,w:800,h:800},400,bearing,1.852*2,catalog.island_outline_m);
    assert.ok(paths.length>0);
    for(const path of paths) {
      const [sx,sy]=path[0], [ex,ey]=path.at(-1);
      assert.ok((ex-sx)*x+(ey-sy)*y>0,'arrow points downstream');
      assert.ok(Math.abs((ex-sx)*y-(ey-sy)*x)<1e-8,'regional flow stays uniform');
    }
  }
});
test('unavailable or weak current never creates fictitious directional arrows', () => {
  const view={x:-600,y:-400,w:1400,h:1600};
  for(const [dir,speed] of [[null,2],[90,null],[NaN,2],[90,-1],[90,0],[90,.1]]) {
    assert.deepEqual(G.flowArrows(view,600,dir,speed,catalog.island_outline_m),[]);
  }
  assert.deepEqual(G.flowArrows(view,600,90,2,catalog.island_outline_m,'off'),[]);
});
test('regional and hypothetical arrows exclude the northern area and land at every bearing', () => {
  const land=catalog.island_outline_m;
  for(const mode of ['regional','local']) for(const width of [320,900]) for(let dir=0;dir<360;dir+=15) {
    const paths=G.flowArrows({x:-650,y:-350,w:1300,h:1500},width,dir,5,land,mode);
    assert.ok(paths.length>0);
    for(const path of paths) for(const [x,y] of path) {
      assert.ok(Number.isFinite(x)&&Number.isFinite(y)&&y>=0);
      assert.equal(G.insideLand(x,y,land),false);
      if(mode==='local') assert.ok(Math.hypot(x,y)>G.illustrativeObstacle(land));
    }
  }
});
test('illustrative flow bends round the obstacle but approaches the forecast direction offshore', () => {
  const v=G.currentVector(90,2), radius=G.illustrativeObstacle(catalog.island_outline_m);
  const near=G.localFlowVector(-radius,radius,v,radius);
  assert.ok(near.y>0,'south-side upstream flow bends south around the island');
  assert.equal(G.localFlowVector(0,0,v,radius),null);
  const far=G.localFlowVector(100000,100000,v,radius);
  assert.ok(Math.abs(far.y)<1e-5 && far.x>.999);
});
test('arrow length increases with forecast speed, and only saturates above four knots', () => {
  const length=kt=>{
    const p=G.flowArrows({x:600,y:600,w:600,h:600},600,90,kt*1.852,catalog.island_outline_m)[0];
    return p.at(-1)[0]-p[0][0];
  };
  assert.ok(length(.5)<length(2));
  assert.ok(length(2)<length(4));
  assert.equal(length(4),length(8));
});
