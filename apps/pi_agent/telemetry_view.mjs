/**
 * How the agent is allowed to read the telemetry artifact.
 *
 * The artifact is a file on disk, and a file is always readable — including
 * long after the simulator that wrote it stopped. Reading it without asking
 * how old it is lets the agent state last hour's altitude in the present
 * tense, which is the same failure as remembering a stale detection as a fact.
 *
 * So the values only leave this module together with their age, and past the
 * freshness bound they do not leave at all: an unknown state is a safe thing
 * to say, while a confidently wrong one is not.
 */

// A dashboard relay writes roughly once a second while a mission runs. Five
// seconds is late enough to tolerate a slow write, early enough that nobody
// hears "it is hovering at 2 m" about a drone that landed.
export const MAX_TELEMETRY_AGE_S = 5;

export function telemetryLine(document, nowMs) {
  if (!document || typeof document !== "object") {
    return { usable: false, summary: "Nincs telemetria-adat.", line: "flight=unknown; altitude_m=unknown; battery_percent=unknown; age=unknown" };
  }
  const age = ageSeconds(document.captured_at, nowMs);
  if (age === null) {
    return {
      usable: false,
      summary: "A telemetria kora ismeretlen, ezért nem tekinthető élőnek.",
      line: "flight=unknown; altitude_m=unknown; battery_percent=unknown; age=unknown",
    };
  }
  if (age > MAX_TELEMETRY_AGE_S) {
    return {
      usable: false,
      summary: `A telemetria ${Math.round(age)} másodperce állt meg, tehát nem élő. Ne beszélj róla jelen időben.`,
      line: `flight=unknown; altitude_m=unknown; battery_percent=unknown; age_s=${Math.round(age)}; stale=true`,
    };
  }
  const position = document.position && typeof document.position === "object" ? document.position : {};
  const flight = document.in_air === true ? "airborne" : document.in_air === false ? "grounded" : "unknown";
  return {
    usable: true,
    summary: "Élő telemetria olvasva.",
    line: `flight=${flight}; altitude_m=${position.relative_altitude_m ?? "unknown"}; `
      + `battery_percent=${document.battery_percent ?? "unknown"}; age_s=${age.toFixed(1)}; stale=false`,
  };
}

function ageSeconds(capturedAt, nowMs) {
  if (typeof capturedAt !== "string") return null;
  const captured = Date.parse(capturedAt);
  if (Number.isNaN(captured)) return null;
  // A capture stamped in the future is a clock problem, not fresher data;
  // clamping to zero keeps it from reading as the newest possible reading.
  return Math.max(0, (nowMs - captured) / 1000);
}
