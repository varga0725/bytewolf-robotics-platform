export type TelemetrySnapshot = {
  position: {
    latitude_deg: number;
    longitude_deg: number;
    absolute_altitude_m: number;
    relative_altitude_m: number | null;
  } | null;
  battery_percent: number | null;
  in_air: boolean | null;
  captured_at: string | null;
};

export type DisplayTelemetry = {
  flight: string;
  altitude: string;
  battery: string;
  position: string;
};

export type TelemetryConnection = {
  state: "ready" | "stale" | "unavailable";
  label: string;
};

const unavailable = "NEM ELÉRHETŐ";

export function formatTelemetry(snapshot: TelemetrySnapshot): DisplayTelemetry {
  return {
    flight: snapshot.in_air === null ? unavailable : snapshot.in_air ? "LEVEGŐBEN" : "FÖLDÖN",
    altitude:
      snapshot.position?.relative_altitude_m === null || snapshot.position === null
        ? unavailable
        : `${snapshot.position.relative_altitude_m.toFixed(1).replace(".", ",")} m`,
    battery:
      snapshot.battery_percent === null
        ? unavailable
        : `${snapshot.battery_percent.toFixed(1).replace(".", ",")} %`,
    position:
      snapshot.position === null
        ? unavailable
        : `${snapshot.position.latitude_deg.toFixed(6).replace(".", ",")}, ${snapshot.position.longitude_deg
            .toFixed(6)
            .replace(".", ",")}`,
  };
}

export function telemetryConnection(
  capturedAt: string | null,
  now: Date = new Date(),
): TelemetryConnection {
  if (capturedAt === null) return { state: "unavailable", label: "TELEMETRIA NEM ELÉRHETŐ" };

  const capturedTime = Date.parse(capturedAt);
  if (Number.isNaN(capturedTime)) return { state: "unavailable", label: "TELEMETRIA NEM ELÉRHETŐ" };

  return now.getTime() - capturedTime > 10_000
    ? { state: "stale", label: "TELEMETRIA ELAVULT" }
    : { state: "ready", label: "TELEMETRIA KAPCSOLÓDVA" };
}
