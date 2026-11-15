import { parseTargetClass, type TargetClass } from "./targetClass";
import { telemetryConnection, type TelemetryConnection } from "./telemetry";

export type MapPosition = { latitude_deg: number; longitude_deg: number };

export type MapHomePosition = MapPosition & {
  absolute_altitude_m: number;
  captured_at: string;
};

export type MapTelemetrySample = {
  position: MapPosition | null;
  home: MapHomePosition | null;
  heading_deg: number | null;
  captured_at: string | null;
  target_class: TargetClass | null;
};

export type MapTrackPoint = MapPosition & { captured_at: string };

export const MAP_TRACK_LIMIT = 240;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function finiteNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function parseHome(value: unknown): MapHomePosition | null {
  if (!isRecord(value)) return null;
  if (
    !finiteNumber(value.latitude_deg)
    || !finiteNumber(value.longitude_deg)
    || !finiteNumber(value.absolute_altitude_m)
    || typeof value.captured_at !== "string"
  ) {
    return null;
  }
  return {
    latitude_deg: value.latitude_deg,
    longitude_deg: value.longitude_deg,
    absolute_altitude_m: value.absolute_altitude_m,
    captured_at: value.captured_at,
  };
}

export function parseMapTelemetry(value: unknown): MapTelemetrySample {
  if (!isRecord(value)) {
    return { position: null, home: null, heading_deg: null, captured_at: null, target_class: null };
  }
  const positionRaw = value.position;
  let position: MapPosition | null = null;
  if (isRecord(positionRaw)
    && finiteNumber(positionRaw.latitude_deg)
    && finiteNumber(positionRaw.longitude_deg)) {
    position = {
      latitude_deg: positionRaw.latitude_deg,
      longitude_deg: positionRaw.longitude_deg,
    };
  }
  const heading_deg = finiteNumber(value.heading_deg) ? value.heading_deg : null;
  const captured_at = typeof value.captured_at === "string" ? value.captured_at : null;
  return {
    position,
    home: parseHome(value.home),
    heading_deg,
    captured_at,
    target_class: parseTargetClass(value.target_class),
  };
}

export function mapTelemetryConnection(sample: MapTelemetrySample, now: Date = new Date()): TelemetryConnection {
  return telemetryConnection(sample.captured_at, now);
}

export function mapHomeAnchor(sample: MapTelemetrySample): MapHomePosition | null {
  return sample.home;
}

export function appendTrackPoint(
  track: MapTrackPoint[],
  sample: MapTelemetrySample,
  connection: TelemetryConnection,
): MapTrackPoint[] {
  if (connection.state !== "ready" || sample.position === null || sample.captured_at === null) {
    return track;
  }
  const next: MapTrackPoint = {
    ...sample.position,
    captured_at: sample.captured_at,
  };
  const last = track.at(-1);
  if (last
    && last.latitude_deg === next.latitude_deg
    && last.longitude_deg === next.longitude_deg
    && last.captured_at === next.captured_at) {
    return track;
  }
  return [...track, next].slice(-MAP_TRACK_LIMIT);
}