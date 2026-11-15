export const MAX_MAP_WAYPOINTS = 11;

export type MapMissionWaypoint = {
  latitude_deg: number;
  longitude_deg: number;
  relative_altitude_m: number;
};

export type MapHomePayload = {
  latitude_deg: number;
  longitude_deg: number;
  absolute_altitude_m: number;
  captured_at: string;
};

export type DemoWaypointMissionPayload = {
  takeoff_altitude_m: number;
  goal: string;
  home: MapHomePayload;
  waypoints: MapMissionWaypoint[];
};

export function buildDemoWaypointMissionPayload(
  waypoints: MapMissionWaypoint[],
  takeoffAltitudeM: number,
  goal: string,
  home: MapHomePayload,
): DemoWaypointMissionPayload {
  return {
    takeoff_altitude_m: takeoffAltitudeM,
    goal: goal.trim(),
    home,
    waypoints: waypoints.map((waypoint) => ({
      latitude_deg: waypoint.latitude_deg,
      longitude_deg: waypoint.longitude_deg,
      relative_altitude_m: waypoint.relative_altitude_m,
    })),
  };
}