/* Presentation only: moon age and harmonic tide heights already in LONGRANGE.
 * Never changes a forecast score, adds tides to currents, or fills missing data.
 */
(function(root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.CalendarVisuals = factory();
})(typeof globalThis !== 'undefined' ? globalThis : this, function() {
  'use strict';
  const SYNODIC_MONTH = 29.530588853;
  const TIDE_HOURS = [8, 9, 10, 11, 12, 13, 14, 15];
  const PHASE_NAMES = ['新月ごろ', '満ちていく月', '上弦ごろ', '満ちていく月', '満月ごろ', '欠けていく月', '下弦ごろ', '欠けていく月'];
  const finite = value => typeof value === 'number' && Number.isFinite(value);
  const escape = value => String(value).replace(/[&<>"']/g, char => ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[char]));

  function moonPhase(age) {
    if (!finite(age) || age < 0 || age >= 30) return null;
    // The source is days since the previous new moon at JST noon. This is a
    // schematic phase, not an ephemeris of the Moon's orientation in the sky.
    const fraction = Math.min(age / SYNODIC_MONTH, 1);
    const cosine = Math.cos(2 * Math.PI * fraction);
    return {
      age, waxing: fraction <= 0.5,
      illumination: (1 - cosine) / 2,
      terminatorRadius: Math.abs(cosine) * 10,
      terminatorOnRight: cosine >= 0,
      name: PHASE_NAMES[Math.round(fraction * 8) % 8],
    };
  }

  function moonLabel(age) {
    const phase = moonPhase(age);
    return phase ? `${phase.name}・月齢 ${age.toFixed(1)}` : '月齢データなし';
  }

  function moonIcon(age, decorative = false) {
    const phase = moonPhase(age);
    if (!phase) return '<span class="cal-moon-missing">—</span>';
    const outerSweep = phase.waxing ? 1 : 0;
    const innerSweep = phase.terminatorOnRight ? 1 - outerSweep : outerSweep;
    const lit = phase.illumination < 0.0001 ? '' : phase.illumination > 0.9999
      ? '<circle class="cal-moon-lit" cx="12" cy="12" r="10"/>'
      : `<path class="cal-moon-lit" d="M12 2 A10 10 0 0 ${outerSweep} 12 22 A${phase.terminatorRadius.toFixed(4)} 10 0 0 ${innerSweep} 12 2Z"/>`;
    return `<svg class="cal-moon" viewBox="0 0 24 24" ${decorative ? 'aria-hidden="true"' : `role="img" aria-label="${escape(moonLabel(age))}"`}><circle class="cal-moon-dark" cx="12" cy="12" r="10"/>${lit}<circle class="cal-moon-edge" cx="12" cy="12" r="10"/></svg>`;
  }

  function tideSeries(day, hours) {
    if (!day || !Array.isArray(hours) || !Array.isArray(day.tide_curve) || hours.length !== day.tide_curve.length) return null;
    if (hours.some((hour, i) => !Number.isInteger(hour) || (i > 0 && hour <= hours[i - 1]))) return null;
    const points = TIDE_HOURS.map(hour => ({hour, level: day.tide_curve[hours.indexOf(hour)]}));
    // Never coerce null, zero-fill gaps, or bridge a missing hour.
    return points.every(point => finite(point.level)) ? points : null;
  }

  function tideScale(days, hours) {
    const levels = (days || []).flatMap(day => (tideSeries(day, hours) || []).map(point => point.level));
    if (!levels.length) return [-1, 1];
    // A shared vertical scale for all 183 days preserves relative tidal range.
    let low = Math.floor(Math.min(0, ...levels) * 2) / 2;
    let high = Math.ceil(Math.max(0, ...levels) * 2) / 2;
    if (high - low < 1) { low -= 0.5; high += 0.5; }
    return [low, high];
  }

  function curvePath(points) {
    if (!Array.isArray(points) || points.length < 2) return '';
    if (points.some((p, i) => !finite(p.x) || !finite(p.y) || (i > 0 && p.x <= points[i - 1].x))) return '';
    const slopes = points.slice(1).map((p, i) => (p.y - points[i].y) / (p.x - points[i].x));
    const derivatives = points.map((p, i) => {
      if (!i) return slopes[0];
      if (i === points.length - 1) return slopes.at(-1);
      const left = slopes[i - 1], right = slopes[i];
      return left * right <= 0 ? 0 : 2 * left * right / (left + right);
    });
    // Monotone cubic interpolation: smooth hourly samples without artificial
    // overshoots or extra high/low tides between source samples.
    let path = `M${points[0].x.toFixed(2)} ${points[0].y.toFixed(2)}`;
    points.slice(1).forEach((p, i) => {
      const previous = points[i], dx = (p.x - previous.x) / 3;
      path += ` C${(previous.x + dx).toFixed(2)} ${(previous.y + derivatives[i] * dx).toFixed(2)} ${
        (p.x - dx).toFixed(2)} ${(p.y - derivatives[i + 1] * dx).toFixed(2)} ${p.x.toFixed(2)} ${p.y.toFixed(2)}`;
    });
    return path;
  }

  function tidePlot(day, hours, scale, compact = false, detailWidth = 620) {
    const series = tideSeries(day, hours);
    if (!series) return `<span class="cal-tide-missing">${compact ? '潮位なし' : '8〜15時の潮位データが揃っていません。'}</span>`;
    const validScale = Array.isArray(scale) && scale.length === 2 && scale.every(finite) && scale[1] > scale[0];
    const [low, high] = validScale ? scale : tideScale([day], hours);
    const W = compact ? 112 : Math.max(280, Math.min(620, finite(detailWidth) ? detailWidth : 620)), H = compact ? 42 : 222;
    const pad = compact ? {left:3, right:3, top:4, bottom:4} : {left:54, right:20, top:24, bottom:37};
    const x = hour => pad.left + (hour - 8) / 7 * (W - pad.left - pad.right);
    const y = level => pad.top + (high - level) / (high - low) * (H - pad.top - pad.bottom);
    const points = series.map(point => ({x:x(point.hour), y:y(point.level)}));
    const path = curvePath(points);
    const label = `${day.date || ''} 8時から15時の天文潮位参考曲線。8時 ${series[0].level.toFixed(2)}メートル、15時 ${series.at(-1).level.toFixed(2)}メートル。平均海面基準。`;
    let axes = '';
    if (compact) {
      if (low <= 0 && high >= 0) axes = `<line class="cal-tide-zero" x1="${x(8)}" y1="${y(0)}" x2="${x(15)}" y2="${y(0)}"/>`;
    } else {
      const ticks = [...new Set([low, (low + high) / 2, high, ...(low <= 0 && high >= 0 ? [0] : [])])].sort((a, b) => a - b);
      axes = ticks.map(level => `<line class="${level === 0 ? 'cal-tide-zero' : 'cal-tide-grid'}" x1="${x(8)}" y1="${y(level)}" x2="${x(15)}" y2="${y(level)}"/><text x="${pad.left - 9}" y="${y(level) + 5}" text-anchor="end">${level.toFixed(1)}</text>`).join('');
      axes += TIDE_HOURS.map(hour => `<text x="${x(hour)}" y="${H - 11}" text-anchor="middle">${hour}時</text>`).join('');
      axes += '<text x="10" y="15">m</text>';
    }
    const marks = compact ? '' : series.map(point => `<circle class="cal-tide-sample" cx="${x(point.hour)}" cy="${y(point.level)}" r="3.4"><title>${point.hour}時 ${point.level.toFixed(2)} m</title></circle>`).join('');
    const plot = `<svg class="cal-tide-plot ${compact ? 'is-mini' : 'is-detail'}" viewBox="0 0 ${W} ${H}" role="img" aria-label="${escape(label)}">${axes}<path class="cal-tide-line" d="${path}" fill="none"/>${marks}</svg>`;
    return compact
      ? `<span class="cal-tide-mini">${plot}<span class="cal-tide-hours" aria-hidden="true"><span>8時</span><span>15時</span></span></span>`
      : `<figure class="cal-tide-detail"><figcaption>天文潮位の参考曲線 <span>8〜15時・JST</span></figcaption>${plot}<p class="cal-tide-caption">平均海面（MSL）基準の調和近似。毎時の値を曲線で結んでいます。現地の流速・潮止まり時刻を示すものではありません。</p></figure>`;
  }

  return {SYNODIC_MONTH, TIDE_HOURS, moonPhase, moonLabel, moonIcon, tideSeries, tideScale, curvePath, tidePlot};
});
