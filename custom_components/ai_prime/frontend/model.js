// Pure schedule-point model (no DOM). Points: [{t: minute 0-1440, v: {channel: percent}}].

export const CHANNEL_ORDER = ["blue", "green", "deep_red", "warm_white", "cool_white"];
export const CHANNEL_COLORS = {
  blue: "#2196f3",
  green: "#4caf50",
  deep_red: "#f44336",
  warm_white: "#ffc107",
  cool_white: "#03a9f4",
};

const DAY = 1440;
const SNAP = 5;
export const MAX_POINTS = 32; // same cap as the server (library.MAX_POINTS)
export const MAX_NAME = 60; // preset name length (studio.MAX_NAME)

const round1 = (x) => Math.round(x * 10) / 10;
const clampPct = (x) => Math.min(100, Math.max(0, x));
const clone = (points) => points.map((p) => ({ t: p.t, v: { ...p.v } }));

/** Interpolated channel mix at minute t (clamped to the first/last point). */
export function valueAt(points, t) {
  const out = {};
  if (!points.length) {
    for (const c of CHANNEL_ORDER) out[c] = 0;
    return out;
  }
  const first = points[0];
  const last = points[points.length - 1];
  if (t <= first.t) return { ...first.v };
  if (t >= last.t) return { ...last.v };
  for (let i = 0; i < points.length - 1; i++) {
    const a = points[i];
    const b = points[i + 1];
    if (t >= a.t && t <= b.t) {
      const f = b.t === a.t ? 0 : (t - a.t) / (b.t - a.t);
      for (const c of CHANNEL_ORDER) out[c] = round1(a.v[c] + (b.v[c] - a.v[c]) * f);
      return out;
    }
  }
  return { ...last.v };
}

/** New point list with an interpolated point at t (unchanged if t exists). */
export function insertPoint(points, t) {
  if (points.some((p) => p.t === t)) return clone(points);
  const result = clone(points);
  result.push({ t, v: valueAt(points, t) });
  result.sort((a, b) => a.t - b.t);
  return result;
}

/**
 * Move point i to minute t (snap 5 min, clamp between neighbours +-5) and
 * multiply all its channels by `scale` (clamped 0-100, one decimal).
 */
export function movePoint(points, i, t, scale) {
  const result = clone(points);
  const lo = i > 0 ? result[i - 1].t + SNAP : 0;
  const hi = i < result.length - 1 ? result[i + 1].t - SNAP : DAY;
  const snapped = Math.round(t / SNAP) * SNAP;
  result[i].t = Math.min(hi, Math.max(lo, snapped));
  for (const c of CHANNEL_ORDER) result[i].v[c] = round1(clampPct(result[i].v[c] * scale));
  return result;
}

/**
 * Drag helper: move point i to minute t and set its overall level (max channel) to `level`.
 * Non-zero points are scaled multiplicatively; an all-zero point gets all channels = level.
 */
export function dragPoint(points, i, t, level) {
  const max = Math.max(...CHANNEL_ORDER.map((c) => points[i].v[c]));
  const lvl = round1(clampPct(level));
  if (max > 0) return movePoint(points, i, t, lvl / max);
  const result = movePoint(points, i, t, 1);
  for (const c of CHANNEL_ORDER) result[i].v[c] = lvl;
  return result;
}

/** Remove point i; never goes below 2 points. */
export function deletePoint(points, i) {
  if (points.length <= 2) return clone(points);
  return clone(points).filter((_, idx) => idx !== i);
}

/** Same rules as the server: 2-32 points, int times 0-1440 strictly increasing, values 0-100 with 1 decimal. */
export function validate(points) {
  const fail = (error, index = null) => ({ ok: false, error, index });
  if (!Array.isArray(points) || points.length < 2) return fail("min_points");
  if (points.length > MAX_POINTS) return fail("max_points");
  let prev = -1;
  for (let i = 0; i < points.length; i++) {
    const p = points[i];
    if (!Number.isInteger(p.t) || p.t < 0 || p.t > DAY) return fail("invalid_time", i);
    if (p.t <= prev) return fail(p.t === prev ? "duplicate_time" : "unordered_time", i);
    prev = p.t;
    for (const c of CHANNEL_ORDER) {
      const v = p.v && p.v[c];
      if (typeof v !== "number" || !Number.isFinite(v) || v < 0 || v > 100) return fail("invalid_value", i);
      if (Math.abs(v * 10 - Math.round(v * 10)) > 1e-6) return fail("invalid_value", i);
    }
  }
  return { ok: true, error: null, index: null };
}

export function fmtTime(min) {
  const m = Math.round(min);
  return `${String(Math.floor(m / 60)).padStart(2, "0")}:${String(m % 60).padStart(2, "0")}`;
}

/** Minutes since midnight in the given IANA time zone (browser zone if omitted/invalid). */
export function nowMinutes(timeZone, date = new Date()) {
  let parts;
  try {
    parts = new Intl.DateTimeFormat("en-GB", { timeZone: timeZone || undefined, hour: "2-digit", minute: "2-digit", hourCycle: "h23" }).formatToParts(date);
  } catch (_) {
    return date.getHours() * 60 + date.getMinutes();
  }
  const get = (type) => Number(parts.find((p) => p.type === type).value);
  return get("hour") * 60 + get("minute");
}

/** ISO date (YYYY-MM-DD) of `date` in the given time zone. */
export function todayIso(timeZone, date = new Date()) {
  try {
    return new Intl.DateTimeFormat("en-CA", { timeZone: timeZone || undefined, year: "numeric", month: "2-digit", day: "2-digit" }).format(date);
  } catch (_) {
    return date.toISOString().slice(0, 10);
  }
}

/** "H:MM" / "HH:MM" -> minutes (0-1440) or null. */
export function parseTime(str) {
  const m = /^(\d{1,2}):(\d{2})$/.exec(String(str).trim());
  if (!m) return null;
  const h = Number(m[1]);
  const min = Number(m[2]);
  if (min > 59) return null;
  const total = h * 60 + min;
  return total > DAY ? null : total;
}

/** Display text for a place: its name, else "lat, lon" with 4 decimals. */
export function formatPlace(loc) {
  if (!loc) return "";
  if (loc.name) return String(loc.name);
  return `${Number(loc.latitude).toFixed(4)}, ${Number(loc.longitude).toFixed(4)}`;
}

/** True when two places have the same coordinates (within 1e-6). */
export function samePlace(a, b) {
  return !!a && !!b && Math.abs(a.latitude - b.latitude) < 1e-6 && Math.abs(a.longitude - b.longitude) < 1e-6;
}

/** Round a numeric entity state for display; non-numeric states pass through. */
export function fmtState(state, digits) {
  const n = Number(state);
  if (state === null || state === undefined || state === "" || !Number.isFinite(n)) return String(state ?? "");
  return n.toFixed(digits);
}

// Approximate light colour per channel (additive RGB) for the preview swatch.
export const CHANNEL_RGB = {
  blue: [40, 90, 255],
  green: [40, 200, 90],
  deep_red: [255, 30, 20],
  warm_white: [255, 190, 120],
  cool_white: [215, 230, 255],
};

/**
 * Approximate colour of a channel mix {channel: percent}: additive RGB, normalised so the
 * brightest component is 255; alpha follows the total intensity. All zero -> dark grey.
 */
export function mixColor(values) {
  let r = 0;
  let g = 0;
  let b = 0;
  let sum = 0;
  for (const c of CHANNEL_ORDER) {
    const v = Math.max(0, Number(values && values[c]) || 0) / 100;
    const [cr, cg, cb] = CHANNEL_RGB[c];
    r += v * cr;
    g += v * cg;
    b += v * cb;
    sum += v * 100;
  }
  const max = Math.max(r, g, b);
  if (max <= 0) return { r: 51, g: 51, b: 51, a: 1, css: "rgb(51, 51, 51)" };
  const k = 255 / max;
  const out = { r: Math.round(r * k), g: Math.round(g * k), b: Math.round(b * k), a: Math.min(1, 0.15 + sum / 250) };
  out.css = `rgba(${out.r}, ${out.g}, ${out.b}, ${Math.round(out.a * 1000) / 1000})`;
  return out;
}

/** Channel values {channel: percent} at minute `m`, linear between the samples of `curve.t` / `series`. */
export function curveAt(curve, series, m) {
  const out = {};
  const ts = (curve && curve.t) || [];
  for (const c of CHANNEL_ORDER) {
    const ys = (series && series[c]) || [];
    const n = Math.min(ts.length, ys.length);
    if (!n) out[c] = 0;
    else if (m <= ts[0]) out[c] = ys[0];
    else if (m >= ts[n - 1]) out[c] = ys[n - 1];
    else {
      let i = 0;
      while (i < n - 2 && ts[i + 1] < m) i++;
      const f = ts[i + 1] === ts[i] ? 0 : (m - ts[i]) / (ts[i + 1] - ts[i]);
      out[c] = ys[i] + (ys[i + 1] - ys[i]) * f;
    }
  }
  return out;
}
