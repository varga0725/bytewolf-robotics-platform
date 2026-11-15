import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";

import { api } from "./api";
import {
  appendTrackPoint,
  chooseHomeAnchor,
  mapTelemetryConnection,
  parseMapTelemetry,
  type MapPosition,
  type MapTrackPoint,
} from "./controlRoomMapTelemetry";
import { globalOffsetMeters, type GeoPoint } from "./localMissionCoordinates";
import { effectiveTargetClassForConnection, type TargetClass } from "./targetClass";
import { TargetClassBadge } from "./TargetClassBadge";

export type ControlRoomMapWaypoint = GeoPoint & { north_m: number; east_m: number };

type Props = {
  ariaLabel: string;
  interactive?: boolean;
  targetClassHint?: TargetClass | null;
  selectionMode?: "single" | "multi";
  pendingTarget?: GeoPoint | null;
  plannedWaypoints?: ControlRoomMapWaypoint[];
  onWaypointSelect?: (waypoint: ControlRoomMapWaypoint) => void;
  onWaypointAppend?: (waypoint: ControlRoomMapWaypoint) => void;
  onClearPending?: () => void;
  onRemoveLastWaypoint?: () => void;
  onClearWaypoints?: () => void;
  pollMs?: number;
};

export function ControlRoomMapPanel({
  ariaLabel,
  interactive = false,
  targetClassHint = null,
  selectionMode = "single",
  pendingTarget = null,
  plannedWaypoints = [],
  onWaypointSelect,
  onWaypointAppend,
  onClearPending,
  onRemoveLastWaypoint,
  onClearWaypoints,
  pollMs = 2_000,
}: Props) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const mapRef = useRef<L.Map | null>(null);
  const droneMarkerRef = useRef<L.Marker | null>(null);
  const pendingMarkerRef = useRef<L.Marker | null>(null);
  const trackLayerRef = useRef<L.Polyline | null>(null);
  const plannedRouteRef = useRef<L.Polyline | null>(null);
  const plannedMarkersRef = useRef<L.LayerGroup | null>(null);
  const [homeAnchor, setHomeAnchor] = useState<MapPosition | null>(null);
  const [track, setTrack] = useState<MapTrackPoint[]>([]);
  const [sample, setSample] = useState(() => parseMapTelemetry(null));
  const connection = mapTelemetryConnection(sample);
  const connectionUi = connection.state === "ready"
    ? "ready"
    : connection.state === "stale"
      ? "stale"
      : "unavailable";
  const multiSelect = selectionMode === "multi";

  const effectiveTarget = effectiveTargetClassForConnection(
    connectionUi,
    targetClassHint ?? sample.target_class,
  );

  const statusMessage = useMemo(() => {
    if (connection.state === "unavailable") {
      return sample.position === null
        ? "Nincs GNSS-javítás: a repülőgép pozíciója nem jeleníthető meg."
        : "A telemetria nem ellenőrizhető; a pozíció rejtve marad.";
    }
    if (connection.state === "stale") {
      return sample.position === null
        ? "A telemetria elavult; nincs érvényes pozíció a mintában."
        : "A telemetria elavult; a térkép nem követi tovább az új mintákat.";
    }
    if (sample.position === null) {
      return "Nincs érvényes pozíció a friss telemetriában.";
    }
    if (interactive && homeAnchor === null) {
      return "Várakozás a kiindulási pozícióra a térképi cél kijelöléséhez.";
    }
    if (multiSelect) {
      return plannedWaypoints.length
        ? `${plannedWaypoints.length} waypoint a listán; kattints további pont hozzáadásához.`
        : "Kattints a térképre az első waypoint hozzáadásához.";
    }
    return "Élő pozíció, iránytartás és nyomvonal.";
  }, [connection.state, homeAnchor, interactive, multiSelect, plannedWaypoints.length, sample.position]);

  const refresh = useCallback(async () => {
    try {
      const payload = await api<unknown>("/api/v1/telemetry");
      setSample(parseMapTelemetry(payload));
    } catch {
      setSample(parseMapTelemetry(null));
    }
  }, []);

  useEffect(() => {
    void refresh();
    const timer = window.setInterval(() => void refresh(), pollMs);
    return () => window.clearInterval(timer);
  }, [pollMs, refresh]);

  useEffect(() => {
    const connectionState = mapTelemetryConnection(sample);
    setHomeAnchor((current) => chooseHomeAnchor(current, sample, connectionState));
    setTrack((current) => appendTrackPoint(current, sample, connectionState));
  }, [sample]);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    const map = L.map(containerRef.current, {
      center: [37.416, -122.049],
      zoom: 17,
      attributionControl: true,
    });
    L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 19,
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).addTo(map);
    plannedMarkersRef.current = L.layerGroup().addTo(map);
    mapRef.current = map;
    return () => {
      map.remove();
      mapRef.current = null;
      droneMarkerRef.current = null;
      pendingMarkerRef.current = null;
      trackLayerRef.current = null;
      plannedRouteRef.current = null;
      plannedMarkersRef.current = null;
    };
  }, []);

  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const showDrone = connection.state === "ready" && sample.position !== null;
    if (!showDrone) {
      droneMarkerRef.current?.remove();
      droneMarkerRef.current = null;
      trackLayerRef.current?.remove();
      trackLayerRef.current = null;
      return;
    }
    const pos = sample.position!;
    const latLng = L.latLng(pos.latitude_deg, pos.longitude_deg);
    const heading = sample.heading_deg ?? 0;
    const icon = L.divIcon({
      className: "control-room-map-drone-icon",
      html: `<div class="control-room-map-drone" style="transform: rotate(${heading}deg)"></div>`,
      iconSize: [28, 28],
      iconAnchor: [14, 14],
    });
    if (droneMarkerRef.current) {
      droneMarkerRef.current.setLatLng(latLng);
      droneMarkerRef.current.setIcon(icon);
    } else {
      droneMarkerRef.current = L.marker(latLng, { icon, interactive: false }).addTo(map);
    }

    if (track.length >= 2) {
      const latlngs = track.map((point) => L.latLng(point.latitude_deg, point.longitude_deg));
      if (trackLayerRef.current) {
        trackLayerRef.current.setLatLngs(latlngs);
      } else {
        trackLayerRef.current = L.polyline(latlngs, { color: "#59dde5", weight: 3, opacity: 0.85 }).addTo(map);
      }
    } else {
      trackLayerRef.current?.remove();
      trackLayerRef.current = null;
    }

    if (!multiSelect && pendingTarget === null && plannedWaypoints.length === 0) {
      map.setView(latLng, Math.max(map.getZoom(), 17), { animate: false });
    }
  }, [connection.state, multiSelect, pendingTarget, plannedWaypoints.length, sample, track]);

  useEffect(() => {
    const map = mapRef.current;
    const layerGroup = plannedMarkersRef.current;
    if (!map || !layerGroup) return;

    layerGroup.clearLayers();
    plannedRouteRef.current?.remove();
    plannedRouteRef.current = null;

    if (multiSelect && plannedWaypoints.length > 0) {
      const latlngs = plannedWaypoints.map((waypoint) => L.latLng(waypoint.latitude_deg, waypoint.longitude_deg));
      plannedRouteRef.current = L.polyline(latlngs, {
        color: "#f3bd56",
        weight: 3,
        dashArray: "6 4",
        opacity: 0.9,
      }).addTo(map);
      plannedWaypoints.forEach((waypoint, index) => {
        const marker = L.marker(L.latLng(waypoint.latitude_deg, waypoint.longitude_deg), {
          interactive: false,
          icon: L.divIcon({
            className: "control-room-map-waypoint-icon",
            html: `<div class="control-room-map-waypoint" aria-hidden="true">${index + 1}</div>`,
            iconSize: [24, 24],
            iconAnchor: [12, 12],
          }),
        });
        layerGroup.addLayer(marker);
      });
    }
  }, [multiSelect, plannedWaypoints]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || multiSelect) return;
    if (pendingTarget === null) {
      pendingMarkerRef.current?.remove();
      pendingMarkerRef.current = null;
      return;
    }
    const latLng = L.latLng(pendingTarget.latitude_deg, pendingTarget.longitude_deg);
    const icon = L.divIcon({
      className: "control-room-map-pending-icon",
      html: '<div class="control-room-map-pending"></div>',
      iconSize: [22, 22],
      iconAnchor: [11, 11],
    });
    if (pendingMarkerRef.current) {
      pendingMarkerRef.current.setLatLng(latLng);
    } else {
      pendingMarkerRef.current = L.marker(latLng, { icon, interactive: false }).addTo(map);
    }
  }, [multiSelect, pendingTarget]);

  useEffect(() => {
    const map = mapRef.current;
    if (!map || !interactive) return;

    const onClick = (event: L.LeafletMouseEvent) => {
      if (homeAnchor === null || connection.state !== "ready") return;
      const point: GeoPoint = { latitude_deg: event.latlng.lat, longitude_deg: event.latlng.lng };
      const offset = globalOffsetMeters(homeAnchor, point);
      const waypoint: ControlRoomMapWaypoint = { ...point, ...offset };
      if (multiSelect) {
        onWaypointAppend?.(waypoint);
      } else {
        onWaypointSelect?.(waypoint);
      }
    };
    map.on("click", onClick);
    return () => {
      map.off("click", onClick);
    };
  }, [connection.state, homeAnchor, interactive, multiSelect, onWaypointAppend, onWaypointSelect]);

  const canPick = interactive && homeAnchor !== null && connection.state === "ready";

  return (
    <section className="control-room-map-panel">
      <div className="control-room-map-head">
        <p className={`telemetry-sample telemetry-sample--${connection.state}`}>{connection.label}</p>
        {effectiveTarget !== null && <TargetClassBadge targetClass={effectiveTarget} />}
        {multiSelect && plannedWaypoints.length > 0 && (
          <div className="control-room-map-actions">
            {onRemoveLastWaypoint && (
              <button type="button" className="control-room-map-clear" onClick={onRemoveLastWaypoint}>
                Utolsó waypoint törlése
              </button>
            )}
            {onClearWaypoints && (
              <button type="button" className="control-room-map-clear" onClick={onClearWaypoints}>
                Waypoint lista törlése
              </button>
            )}
          </div>
        )}
        {!multiSelect && pendingTarget && onClearPending && (
          <button type="button" className="control-room-map-clear" onClick={onClearPending}>
            Függő cél törlése
          </button>
        )}
      </div>
      <div
        ref={containerRef}
        className={`control-room-map${interactive ? " control-room-map--interactive" : ""}${canPick ? "" : " control-room-map--pick-disabled"}`}
        data-testid="control-room-map"
        role="application"
        aria-label={ariaLabel}
        aria-disabled={interactive ? !canPick : undefined}
      />
      <p className="muted" role="status">{statusMessage}</p>
    </section>
  );
}