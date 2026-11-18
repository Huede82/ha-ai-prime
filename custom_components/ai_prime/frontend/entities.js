// Find the entities of one lamp (config entry) in hass.entities.
//
// hass.entities holds entity registry display entries (entity_id, device_id,
// platform, translation_key); they carry no config_entry_id, so the lamp is
// resolved through hass.devices[device_id].config_entries. Entities are picked
// by translation_key (set by the integration, = unique_id suffix). If a
// translation_key is missing, the entity_id suffix `_<key>` is the fallback.

import { CHANNEL_ORDER } from "./model.js";

export const KEYS = ["light", "nature_mode", "temperature", "fan", "power", "cloud_cover", ...CHANNEL_ORDER];

const DOMAIN_OF = { light: "light", nature_mode: "switch", fan: "binary_sensor", temperature: "sensor", power: "sensor", cloud_cover: "sensor" };
for (const c of CHANNEL_ORDER) DOMAIN_OF[c] = "number";

/** -> {key: entity_id} for the lamp of `entryId`. */
export function findEntities(hass, entryId) {
  const found = {};
  const entities = (hass && hass.entities) || {};
  const devices = (hass && hass.devices) || {};
  for (const [entityId, entry] of Object.entries(entities)) {
    if (entry.platform !== "ai_prime") continue;
    const device = devices[entry.device_id];
    if (!device || !(device.config_entries || []).includes(entryId)) continue;
    let key = entry.translation_key;
    if (!KEYS.includes(key)) {
      key = KEYS.find((k) => entityId.startsWith(`${DOMAIN_OF[k]}.`) && entityId.endsWith(`_${k}`));
    }
    if (key && DOMAIN_OF[key] === entityId.split(".")[0] && !(key in found)) found[key] = entityId;
  }
  return found;
}
