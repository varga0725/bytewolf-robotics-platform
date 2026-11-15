export type GeoPoint = { latitude_deg: number; longitude_deg: number };
export type LocalOffset = { north_m: number; east_m: number };

// Must match brain/navigation/waypoints.py (R = 6 371 000 m), which plans the mission.
const METERS_PER_DEGREE_LAT = (6_371_000 * Math.PI) / 180;

function metersPerDegreeLon(latitude_deg: number): number {
  return METERS_PER_DEGREE_LAT * Math.cos((latitude_deg * Math.PI) / 180);
}

/** Flat-earth offset from home to a point; matches the mission map's local N/E frame. */
export function globalOffsetMeters(home: GeoPoint, point: GeoPoint): LocalOffset {
  const north_m = (point.latitude_deg - home.latitude_deg) * METERS_PER_DEGREE_LAT;
  const east_m = (point.longitude_deg - home.longitude_deg) * metersPerDegreeLon(home.latitude_deg);
  return { north_m, east_m };
}

export function pointFromLocalOffset(home: GeoPoint, offset: LocalOffset): GeoPoint {
  return {
    latitude_deg: home.latitude_deg + offset.north_m / METERS_PER_DEGREE_LAT,
    longitude_deg: home.longitude_deg + offset.east_m / metersPerDegreeLon(home.latitude_deg),
  };
}