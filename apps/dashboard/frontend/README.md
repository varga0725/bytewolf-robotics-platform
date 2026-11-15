# Control Room frontend

## OpenStreetMap tiles

The live map panels load raster tiles from `https://tile.openstreetmap.org/`. The browser must have internet access while the dashboard is open. Tile requests expose the viewer's IP address and approximate map viewport to OpenStreetMap's CDN (acceptable for local SITL; do not use on air-gapped Ground deployments without an offline tile source).

Leaflet renders the mandatory attribution in the map corner: **© OpenStreetMap contributors**.

## Map planning contract

Mission map clicks are disabled until PX4 `HOME_POSITION` appears in `/api/v1/telemetry` as `home`. Proposals send geographic waypoint coordinates plus that home reference; the API recomputes north/east on the server.