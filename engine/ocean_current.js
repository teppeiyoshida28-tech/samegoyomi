/* Pure display adapter. Regional speed is never inferred from the schematic field. */
(function (root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else root.OceanCurrent = api;
})(typeof globalThis !== 'undefined' ? globalThis : this, function () {
  'use strict';
  const HOURS = Object.freeze([8, 9, 10, 11, 12, 13, 14]);
  const MAX_KT = 6;
  const COLOR_STOPS = Object.freeze([
    [0, [64, 133, 224]], [1.5, [61, 184, 201]], [3, [157, 207, 123]],
    [4.5, [247, 198, 107]], [6, [255, 162, 171]]
  ]);
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  function timestamp(value) {
    if (typeof value !== 'string' || !/(?:Z|[+-]\d{2}:\d{2})$/.test(value)) return NaN;
    return Date.parse(value);
  }
  function jstDate(value) {
    const time = typeof value === 'number' ? value : timestamp(value);
    return Number.isFinite(time) ? new Date(time + 9 * 3600000).toISOString().slice(0, 10) : null;
  }
  function targetTime(day, hour) {
    return /^\d{4}-\d{2}-\d{2}$/.test(day || '') && HOURS.includes(hour)
      ? `${day}T${String(hour).padStart(2, '0')}:00:00+09:00` : null;
  }
  function bundleStatus(bundle, now = Date.now()) {
    if (!bundle || bundle.schema_version !== 1) return 'missing';
    const retrieved = timestamp(bundle.retrieved_at), expires = timestamp(bundle.expires_at);
    if (!Number.isFinite(retrieved) || !Number.isFinite(expires) || expires <= retrieved || retrieved > now + 300000) return 'missing';
    if (now >= expires) return 'expired';
    if (!['available', 'partial'].includes(bundle.status)) return 'missing';
    if (bundle.source?.velocity_unit !== 'km/h' || bundle.source?.direction_convention !== 'towards') return 'missing';
    return 'available';
  }
  function hourKey(record) {
    const time = timestamp(record?.time);
    if (!Number.isFinite(time)) return null;
    const date = new Date(time + 9 * 3600000);
    if (date.getUTCMinutes() || date.getUTCSeconds() || date.getUTCMilliseconds()) return null;
    const hour = date.getUTCHours();
    return HOURS.includes(hour) ? targetTime(date.toISOString().slice(0, 10), hour) : null;
  }
  function prepare(bundle, now = Date.now()) {
    const records = new Map();
    for (const record of bundle?.hourly || []) {
      const key = hourKey(record);
      // Conflicting duplicate timestamps must not silently choose one forecast.
      if (key) records.set(key, records.has(key) ? null : record);
    }
    const today = jstDate(now), last = jstDate(bundle?.coverage?.last_valid_time);
    const days = [];
    if (today && last && last >= today) {
      for (let time = timestamp(`${today}T00:00:00+09:00`); days.length < 16; time += 86400000) {
        const day = jstDate(time);
        if (day > last) break;
        if (HOURS.some(hour => validRecord(records.get(targetTime(day, hour))))) days.push(day);
        if (time >= timestamp(`${today}T00:00:00+09:00`) + 15 * 86400000) break;
      }
    }
    return { days, records };
  }
  function validRecord(record) {
    return record?.status === 'available' && finite(record.current_velocity_kmh) && record.current_velocity_kmh >= 0 && finite(record.current_direction) && record.current_direction >= 0 && record.current_direction <= 360;
  }
  function select(bundle, prepared, day, hour, now = Date.now()) {
    const time = targetTime(day, hour), record = time ? prepared.records.get(time) : null;
    let status = bundleStatus(bundle, now);
    const speed = record?.current_velocity_kmh, direction = record?.current_direction;
    if (status === 'available' && !validRecord(record)) status = 'missing';
    return {
      status, time, isPast: Number.isFinite(timestamp(time)) && timestamp(time) < now,
      kt: status === 'available' ? speed / 1.852 : null,
      direction: status === 'available' ? direction % 360 : null,
      retrievedAt: bundle?.retrieved_at || null, expiresAt: bundle?.expires_at || null,
      modelRunAt: bundle?.source?.model_run_at || null
    };
  }
  function strengthStyle(value, displayWidth = 600) {
    if (!finite(value) || value < 0) return { valid: false, moving: false, kt: null, cappedKt: null, rgb: [100, 117, 130], length: 0, speed: 0, time: 0 };
    const k = Math.min(MAX_KT, value), i = Math.min(COLOR_STOPS.length - 2, Math.floor(k / 1.5));
    const [lo, a] = COLOR_STOPS[i], [hi, b] = COLOR_STOPS[i + 1], t = (k - lo) / (hi - lo);
    const rgb = a.map((v, j) => Math.round(v + (b[j] - v) * t));
    const factor = Math.max(.67, Math.min(1, (finite(displayWidth) ? displayWidth : 600) / 600));
    const moving = value >= .2, length = moving ? (8 + 10 * k) * factor : 0, speed = moving ? (18 + 10 * k) * factor : 0;
    return { valid: true, moving, kt: value, cappedKt: k, rgb, length, speed, time: moving ? length / speed : 0 };
  }
  function vector(direction) {
    if (!finite(direction)) return null;
    const angle = direction * Math.PI / 180;
    return [Math.sin(angle), -Math.cos(angle)];
  }
  function decodeFields(flow) {
    if (flow?.schemaVersion !== 2 || flow.basis?.normalizedAtNodes !== false) throw new Error('Unsupported schematic field');
    const count = flow.grid.nx * flow.grid.ny * 2, fields = {};
    for (const key of ['ne', 'west']) {
      const field = flow.fields[key];
      if (field?.encoding !== 'int16le-interleaved-uv' || !finite(field.scale) || field.scale <= 0) throw new Error('Invalid direction basis');
      const raw = atob(field.data), bytes = Uint8Array.from(raw, c => c.charCodeAt(0));
      if (bytes.length !== count * 2) throw new Error('Invalid direction grid size');
      const view = new DataView(bytes.buffer), values = new Float32Array(count);
      for (let i = 0; i < count; i++) values[i] = view.getInt16(i * 2, true) / field.scale;
      fields[key] = values;
    }
    return fields;
  }
  function sampleField(flow, fields, x, y, direction) {
    if (!finite(direction) || !finite(x) || !finite(y)) return null;
    const grid = flow.grid, bounds = flow.drawBounds;
    if (x < bounds[0] || y < bounds[1] || x > bounds[0] + bounds[2] || y > bounds[1] + bounds[3]) return null;
    const gx = (x - grid.origin[0]) / grid.spacing, gy = (y - grid.origin[1]) / grid.spacing;
    const ix = Math.floor(gx), iy = Math.floor(gy);
    if (ix < 0 || iy < 0 || ix >= grid.nx - 1 || iy >= grid.ny - 1) return null;
    const tx = gx - ix, ty = gy - iy;
    const indices = [(iy * grid.nx + ix) * 2, (iy * grid.nx + ix + 1) * 2, ((iy + 1) * grid.nx + ix) * 2, ((iy + 1) * grid.nx + ix + 1) * 2];
    const weights = [(1 - tx) * (1 - ty), tx * (1 - ty), (1 - tx) * ty, tx * ty];
    const angle = direction * Math.PI / 180, a = Math.SQRT2 * Math.cos(angle), b = Math.cos(angle) - Math.sin(angle);
    let u = 0, v = 0;
    for (let i = 0; i < 4; i++) {
      u += (a * fields.ne[indices[i]] + b * fields.west[indices[i]]) * weights[i];
      v += (a * fields.ne[indices[i] + 1] + b * fields.west[indices[i] + 1]) * weights[i];
    }
    const norm = Math.hypot(u, v);
    return Number.isFinite(norm) && norm >= (flow.rendering?.minimumCombinedNorm || .025) ? [u / norm, v / norm] : null;
  }
  return { HOURS, MAX_KT, COLOR_STOPS, timestamp, jstDate, targetTime, bundleStatus, prepare, validRecord, select, strengthStyle, vector, decodeFields, sampleField };
});
