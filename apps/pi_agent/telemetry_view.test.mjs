import assert from "node:assert/strict";
import test from "node:test";

import { MAX_TELEMETRY_AGE_S, telemetryLine } from "./telemetry_view.mjs";

const CAPTURED = "2026-07-20T12:00:00Z";
const CAPTURED_MS = Date.parse(CAPTURED);

function snapshot(extra = {}) {
  return {
    captured_at: CAPTURED,
    in_air: true,
    battery_percent: 74,
    position: { latitude_deg: 47.4, longitude_deg: 8.5, relative_altitude_m: 2.4 },
    ...extra,
  };
}

test("fresh telemetry is reported with its age attached", () => {
  const view = telemetryLine(snapshot(), CAPTURED_MS + 1_200);

  assert.equal(view.usable, true);
  assert.match(view.line, /flight=airborne/);
  assert.match(view.line, /altitude_m=2\.4/);
  assert.match(view.line, /age_s=1\.2/);
  assert.match(view.line, /stale=false/);
});

test("stale telemetry becomes unknown rather than last hour's altitude", () => {
  const view = telemetryLine(snapshot(), CAPTURED_MS + (MAX_TELEMETRY_AGE_S + 1) * 1000);

  assert.equal(view.usable, false);
  assert.match(view.line, /altitude_m=unknown/);
  assert.match(view.line, /stale=true/);
  assert.match(view.summary, /nem élő/);
});

test("telemetry without a capture time cannot be called live", () => {
  const view = telemetryLine(snapshot({ captured_at: undefined }), CAPTURED_MS);

  assert.equal(view.usable, false);
  assert.match(view.summary, /kora ismeretlen/);
});

test("a capture stamped in the future is not the freshest possible reading", () => {
  const view = telemetryLine(snapshot(), CAPTURED_MS - 60_000);

  assert.equal(view.usable, true, "a small clock skew stays usable");
  assert.match(view.line, /age_s=0\.0/);
});

test("a missing artifact reports unknown, never a default of zero", () => {
  const view = telemetryLine(null, CAPTURED_MS);

  assert.equal(view.usable, false);
  assert.match(view.line, /battery_percent=unknown/);
  assert.doesNotMatch(view.line, /battery_percent=0/);
});
