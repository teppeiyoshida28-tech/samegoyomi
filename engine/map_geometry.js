/* Pure schematic geometry, in model metres. No viewport clamping of features. */
const MapGeometry = (() => {
  function shelfRadius(radii, bearing) {
    const b = ((bearing % 360) + 360) % 360;
    const lo = Math.floor(b / 30) * 30, t = (b - lo) / 30;
    return radii[lo] * (1 - t) + radii[(lo + 30) % 360] * t;
  }
  function hypothesis(radii, direction, speedKmh) {
    const r = direction * Math.PI / 180, ux = Math.sin(r), uy = -Math.cos(r);
    const kt = speedKmh / 1.852;
    const down = shelfRadius(radii, direction);
    const half = (shelfRadius(radii, direction + 90) + shelfRadius(radii, direction - 90)) / 2;
    const front = (down + .55 * half) * (.8 + .4 * Math.min(1, kt / 3));
    const t = Math.max(0, Math.min(1, (kt - 2) / 2.5));
    const distance = front * (.92 - .28 * t);
    const side = ux >= 0 ? 1 : -1, flank = half * (.85 + .15 * t);
    const zone = {
      x: ux * distance - side * uy * flank,
      y: uy * distance + side * ux * flank,
      rx: Math.min(150 / .55, front * (.42 - .12 * t)),
      ry: Math.max(70 / .55, Math.min(120 / .55, half * (.55 - .15 * t))),
      rotation: direction + 90,
    };
    return {ux, uy, kt, front, half, zone,
      lee: {x: ux * (down * .5 + front * .45), y: uy * (down * .5 + front * .45),
            rx: front * .42, ry: Math.max(70 / .55, half * .8), rotation: direction + 90}};
  }
  function ellipseBounds(e) {
    const r = e.rotation * Math.PI / 180;
    const dx = Math.hypot(e.rx * Math.cos(r), e.ry * Math.sin(r));
    const dy = Math.hypot(e.rx * Math.sin(r), e.ry * Math.cos(r));
    return {left: e.x - dx, right: e.x + dx, top: e.y - dy, bottom: e.y + dy};
  }
  function union(boxes) {
    return {left: Math.min(...boxes.map(b => b.left)), right: Math.max(...boxes.map(b => b.right)),
      top: Math.min(...boxes.map(b => b.top)), bottom: Math.max(...boxes.map(b => b.bottom))};
  }
  function fit(bounds, width, height) {
    const aspect = width / height;
    let w = (bounds.right - bounds.left) * 1.2, h = (bounds.bottom - bounds.top) * 1.2;
    if (w / h < aspect) w = h * aspect; else h = w / aspect;
    return {x: (bounds.left + bounds.right - w) / 2, y: (bounds.top + bounds.bottom - h) / 2, w, h};
  }
  function zoom(view, factor) {
    const w = Math.max(180, Math.min(6000, view.w * factor));
    const h = view.h * w / view.w;
    return {x: view.x + (view.w - w) / 2, y: view.y + (view.h - h) / 2, w, h};
  }
  function contains(view, bounds) {
    return bounds.left >= view.x && bounds.right <= view.x + view.w &&
      bounds.top >= view.y && bounds.bottom <= view.y + view.h;
  }
  function scoreColor(score) {
    const s = Math.max(0, Math.min(1, score));
    const stops = [[143,163,176], [111,174,158], [201,165,78]];
    const i = s < .5 ? 0 : 1, t = (s - i * .5) * 2;
    return `rgb(${stops[i].map((v,j) => Math.round(v + (stops[i+1][j] - v) * t)).join(',')})`;
  }
  function scaleBar(viewWidth, pixelWidth) {
    const metresPerPixel = viewWidth / pixelWidth;
    const max = viewWidth * .22;
    const metres = [10,20,50,100,200,500,1000].filter(n => n <= max).pop() || 10;
    return {metres, pixels: metres / metresPerPixel};
  }
  return {shelfRadius, hypothesis, ellipseBounds, union, fit, zoom, contains, scoreColor, scaleBar};
})();
if (typeof module !== 'undefined') module.exports = MapGeometry;
