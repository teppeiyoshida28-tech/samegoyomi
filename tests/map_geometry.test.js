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
