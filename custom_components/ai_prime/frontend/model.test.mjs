import test from "node:test";
import assert from "node:assert/strict";
import {
  CHANNEL_ORDER, CHANNEL_COLORS, valueAt, insertPoint, movePoint, dragPoint, deletePoint,
  validate, fmtTime, parseTime, formatPlace, samePlace, MAX_POINTS,
} from "./model.js";
import { errorText, translator } from "./i18n.js";

const mix = (n) => Object.fromEntries(CHANNEL_ORDER.map((c) => [c, n]));
const pts = () => [
  { t: 0, v: mix(0) },
  { t: 600, v: mix(60) },
  { t: 1200, v: mix(20) },
];

test("channel order and colors", () => {
  assert.deepEqual(CHANNEL_ORDER, ["blue", "green", "deep_red", "warm_white", "cool_white"]);
  assert.equal(CHANNEL_COLORS.blue, "#2196f3");
  assert.equal(CHANNEL_COLORS.green, "#4caf50");
  assert.equal(CHANNEL_COLORS.deep_red, "#f44336");
  assert.equal(CHANNEL_COLORS.warm_white, "#ffc107");
  assert.equal(CHANNEL_COLORS.cool_white, "#03a9f4");
});

test("valueAt interpolates and clamps outside", () => {
  assert.equal(valueAt(pts(), 300).blue, 30);
  assert.equal(valueAt(pts(), 600).green, 60);
  assert.equal(valueAt(pts(), 900).blue, 40);
  assert.equal(valueAt(pts(), -5).blue, 0);
  assert.equal(valueAt(pts(), 1440).blue, 20);
});

test("insertPoint adds an interpolated point in order", () => {
  const r = insertPoint(pts(), 300);
  assert.equal(r.length, 4);
  assert.equal(r[1].t, 300);
  assert.equal(r[1].v.deep_red, 30);
  assert.equal(insertPoint(pts(), 600).length, 3); // existing time: unchanged
});

test("insertPoint rounds to 0.1 and does not mutate", () => {
  const p = pts();
  const r = insertPoint(p, 100);
  assert.equal(r[1].v.blue, 10);
  assert.equal(p.length, 3);
  const r2 = insertPoint([{ t: 0, v: mix(0) }, { t: 30, v: mix(1) }], 10);
  assert.equal(r2[1].v.blue, 0.3);
});

test("movePoint snaps to 5 minutes", () => {
  assert.equal(movePoint(pts(), 1, 603, 1)[1].t, 605);
  assert.equal(movePoint(pts(), 1, 602, 1)[1].t, 600);
});

test("movePoint clamps between neighbours +-5", () => {
  assert.equal(movePoint(pts(), 1, -100, 1)[1].t, 5);
  assert.equal(movePoint(pts(), 1, 5000, 1)[1].t, 1195);
  assert.equal(movePoint(pts(), 0, -50, 1)[0].t, 0);
  assert.equal(movePoint(pts(), 2, 5000, 1)[2].t, 1440);
});

test("movePoint scales all channels, clamps 0-100, rounds 0.1", () => {
  assert.equal(movePoint(pts(), 1, 600, 0.5)[1].v.blue, 30);
  assert.equal(movePoint(pts(), 1, 600, 10)[1].v.green, 100);
  assert.equal(movePoint(pts(), 1, 600, -1)[1].v.green, 0);
  assert.equal(movePoint(pts(), 1, 600, 1 / 3)[1].v.blue, 20);
  const p = [{ t: 0, v: mix(0) }, { t: 60, v: mix(33.3) }, { t: 120, v: mix(0) }];
  assert.equal(movePoint(p, 1, 60, 1.01)[1].v.blue, 33.6);
});

test("deletePoint keeps at least 2 points", () => {
  assert.equal(deletePoint(pts(), 1).length, 2);
  const two = deletePoint(pts(), 1);
  assert.equal(deletePoint(two, 0).length, 2);
});

test("validate", () => {
  assert.deepEqual(validate(pts()), { ok: true, error: null, index: null });
  assert.equal(validate([pts()[0]]).ok, false);
  const dup = [pts()[0], { t: 0, v: mix(1) }];
  assert.equal(validate(dup).index, 1);
  const unordered = [pts()[1], pts()[0]];
  assert.equal(validate(unordered).ok, false);
  assert.equal(validate(unordered).index, 1);
  assert.equal(validate([pts()[0], { t: 1441, v: mix(1) }]).index, 1);
  assert.equal(validate([pts()[0], { t: 10, v: mix(101) }]).index, 1);
  assert.equal(validate([pts()[0], { t: 10, v: mix(-1) }]).ok, false);
  assert.equal(validate([pts()[0], { t: 10, v: mix(1.25) }]).ok, false);
  const missing = { t: 10, v: { blue: 1 } };
  assert.equal(validate([pts()[0], missing]).ok, false);
  assert.equal(validate([pts()[0], { t: 10.5, v: mix(1) }]).ok, false);
  assert.equal(validate([pts()[0], { t: 1440, v: mix(1) }]).ok, true);
  const many = (n) => Array.from({ length: n }, (_, i) => ({ t: i * 10, v: mix(1) }));
  assert.equal(MAX_POINTS, 32);
  assert.equal(validate(many(32)).ok, true);
  assert.deepEqual(validate(many(33)), { ok: false, error: "max_points", index: null });
});

test("errorText translates known websocket codes", () => {
  const de = translator("de");
  const en = translator("en-GB");
  assert.equal(errorText({ code: "lamp_rejected", message: "Lamp 1" }, de), de("err_lamp_rejected"));
  assert.match(errorText({ code: "lamp_rejected", message: "Lamp 1" }, en), /rejected/);
  assert.equal(errorText({ code: "x", message: "boom" }, en), "boom");
  assert.equal(errorText({ code: "x" }, en), "x");
  assert.equal(errorText("plain", en), "plain");
  assert.match(de("err_max_points"), /32/);
});

test("fmtTime / parseTime", () => {
  assert.equal(fmtTime(0), "00:00");
  assert.equal(fmtTime(605), "10:05");
  assert.equal(fmtTime(1440), "24:00");
  assert.equal(parseTime("10:05"), 605);
  assert.equal(parseTime("7:30"), 450);
  assert.equal(parseTime("24:00"), 1440);
  assert.equal(parseTime("24:01"), null);
  assert.equal(parseTime("12:60"), null);
  assert.equal(parseTime("abc"), null);
});

import { findEntities } from "./entities.js";

test("findEntities picks the lamp's entities by translation_key", () => {
  const hass = {
    devices: { d1: { config_entries: ["e1"] }, d2: { config_entries: ["e2"] } },
    entities: {
      "light.a": { platform: "ai_prime", device_id: "d1", translation_key: "light" },
      "light.b": { platform: "ai_prime", device_id: "d2", translation_key: "light" },
      "number.a_blue": { platform: "ai_prime", device_id: "d1", translation_key: "blue" },
      "sensor.a_temp": { platform: "ai_prime", device_id: "d1", translation_key: "temperature" },
      "switch.a_nm": { platform: "ai_prime", device_id: "d1" , translation_key: "nature_mode" },
      "sensor.a_power": { platform: "ai_prime", device_id: "d1" },
      "sensor.other": { platform: "other", device_id: "d1", translation_key: "power" },
    },
  };
  const f = findEntities(hass, "e1");
  assert.equal(f.light, "light.a");
  assert.equal(f.blue, "number.a_blue");
  assert.equal(f.temperature, "sensor.a_temp");
  assert.equal(f.nature_mode, "switch.a_nm");
  assert.equal(f.power, "sensor.a_power"); // suffix fallback
  assert.equal(findEntities(hass, "e2").light, "light.b");
  assert.deepEqual(findEntities({}, "e1"), {});
});

test("formatPlace and samePlace", () => {
  assert.equal(formatPlace({ name: "Bern", latitude: 1, longitude: 2 }), "Bern");
  assert.equal(formatPlace({ latitude: 46.94809, longitude: 7.4474 }), "46.9481, 7.4474");
  assert.equal(formatPlace(null), "");
  assert.ok(samePlace({ latitude: 1, longitude: 2 }, { latitude: 1, longitude: 2, name: "x" }));
  assert.ok(!samePlace({ latitude: 1, longitude: 2 }, { latitude: 1, longitude: 2.1 }));
  assert.ok(!samePlace(null, { latitude: 1, longitude: 2 }));
});

test("dragPoint scales non-zero points and lifts all-zero points to the level", () => {
  const p = pts();
  p[1].v.blue = 30; // mixed channels keep their ratio
  const scaled = dragPoint(p, 1, 600, 60);
  assert.equal(scaled[1].v.blue, 30 * (60 / 60) );
  const half = dragPoint(p, 1, 600, 30);
  assert.equal(half[1].v.blue, 15);
  assert.equal(half[1].v.green, 30);
  const lifted = dragPoint(pts(), 0, 0, 42.34);
  for (const c of CHANNEL_ORDER) assert.equal(lifted[0].v[c], 42.3);
  assert.equal(dragPoint(pts(), 0, 0, 150)[0].v.blue, 100);
});

import { nowMinutes, todayIso } from "./model.js";
test("nowMinutes and todayIso honour the time zone", () => {
  const d = new Date("2026-11-18T23:30:00Z");
  assert.equal(nowMinutes("UTC", d), 23 * 60 + 30);
  assert.equal(nowMinutes("Asia/Tokyo", d), 8 * 60 + 30);
  assert.equal(todayIso("UTC", d), "2026-11-18");
  assert.equal(todayIso("Asia/Tokyo", d), "2026-11-19");
});

import { fmtState } from "./model.js";
test("fmtState rounds numeric states and passes others through", () => {
  assert.equal(fmtState("39.5059", 1), "39.5");
  assert.equal(fmtState("5.241203", 0), "5");
  assert.equal(fmtState("unavailable", 1), "unavailable");
  assert.equal(fmtState(null, 1), "");
});
