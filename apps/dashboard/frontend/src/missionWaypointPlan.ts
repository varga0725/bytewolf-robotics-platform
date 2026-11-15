export type MapMissionWaypoint = {
  north_m: number;
  east_m: number;
  relative_altitude_m: number;
};

export type DemoWaypointMissionPayload = {
  takeoff_altitude_m: number;
  goal: string;
  waypoints: MapMissionWaypoint[];
};

export function buildDemoWaypointMissionPayload(
  waypoints: MapMissionWaypoint[],
  takeoffAltitudeM: number,
  goal: string,
): DemoWaypointMissionPayload {
  return {
    takeoff_altitude_m: takeoffAltitudeM,
    goal: goal.trim(),
    waypoints: waypoints.map((waypoint) => ({
      north_m: waypoint.north_m,
      east_m: waypoint.east_m,
      relative_altitude_m: waypoint.relative_altitude_m,
    })),
  };
}