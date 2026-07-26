import { expect, test } from "@playwright/test";

const staleTelemetry = {
  position: null,
  battery_percent: 74,
  in_air: false,
  heading_deg: 180,
  captured_at: "2020-01-01T00:00:00.000Z",
};

const knowledge = {
  boundary: "A személyes memória és a világ-bizonyíték külön tároló.",
  personal: {
    namespace: "personal:",
    nodes: [{ id: "personal:operator", label: "Operátor", kind: "person", detail: "Session állítás" }],
    edges: [],
  },
  world: {
    namespace: "world:",
    nodes: [{ id: "world:source:lidar", label: "Front lidar", kind: "source", detail: "Mért forrás" }],
    edges: [],
  },
};

const missionEnvelope = {
  max_altitude_m: 20,
  max_radius_m: 100,
  minimum_battery_percent_to_start: 40,
  geofence_vertices_m: [
    { north_m: -80, east_m: -80 },
    { north_m: 80, east_m: -80 },
    { north_m: 80, east_m: 80 },
    { north_m: -80, east_m: 80 },
  ],
};

const checkedMissionPlan = {
  plan_id: "plan-e2e-001",
  summary: "E2E ellenőrzött pontküldetés",
  goal: "Biztonságos ellenőrző pont",
  steps: ["SafetyGate korlátok ellenőrzése", "Célpont előkészítése"],
  waypoints: [{ north_m: 5, east_m: 0, altitude_m: 2 }],
};

async function mockMissionReadModels(page: import("@playwright/test").Page) {
  await page.route("**/api/v1/safety-envelope", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(missionEnvelope) });
  });
  await page.route("**/api/v1/map-view/meta", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({}) });
  });
  await page.route("**/api/v1/world-map", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ occupancy_only: true, cells: [] }) });
  });
}

test.beforeEach(async ({ page }) => {
  await page.route("**/api/v1/telemetry", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(staleTelemetry) });
  });
  await page.route("**/api/v1/knowledge", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(knowledge) });
  });
  await page.goto("/");
});

test("stale telemetry is visibly labelled as non-current", async ({ page }) => {
  const telemetry = page.getByRole("region", { name: "Élő telemetria állapot" });
  await expect(telemetry).toContainText("Telemetria elavult");
  await expect(telemetry).toContainText("nem aktuális");
  await expect(page.getByText("ELAVULT MINTA").first()).toBeVisible();
});

test("quick navigation reaches the read-only knowledge view", async ({ page }) => {
  await page.getByRole("button", { name: "Gyors navigáció megnyitása" }).focus();
  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog", { name: "Gyors nézetváltó" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("combobox", { name: "Nézet keresése" }).fill("tudás");
  await dialog.getByRole("option", { name: "Tudás" }).click();

  await expect(page.getByRole("tab", { name: "Tudás" })).toHaveAttribute("aria-selected", "true");
  await expect(page.getByRole("heading", { name: "Tudásgráf" })).toBeVisible();
  await expect(page.getByRole("region", { name: "Személyes memória gráf" })).toContainText("Operátor");
  await expect(page.getByRole("region", { name: "Világ-bizonyíték gráf" })).toContainText("Front lidar");
  await expect(page.getByText(/nem módosít sem személyes memóriát/i)).toBeVisible();
});

test("a SafetyGate-terv külön, explicit operátori jóváhagyásig nem indít küldetést", async ({ page }) => {
  let approvalRequests = 0;
  await mockMissionReadModels(page);
  await page.route("**/api/v1/missions/point", async (route) => {
    await expect(route.request().postDataJSON()).toMatchObject({ north_m: 5, east_m: 0, altitude_m: 2 });
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(checkedMissionPlan) });
  });
  await page.route("**/api/v1/plans/approve", async (route) => {
    approvalRequests += 1;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "E2E: nem szabad indítani" }) });
  });

  await page.getByRole("tab", { name: "Küldetés" }).click();
  await expect(page.getByText("Aktív korlát: max. 20 m magasság")).toBeVisible();
  await page.getByRole("button", { name: "Terv ellenőrzése" }).click();

  const approval = page.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" });
  await expect(approval).toBeVisible();
  await expect(page.getByText("Ez a terv még nem indult el.")).toBeVisible();
  expect(approvalRequests).toBe(0);
});

test("a jóváhagyás utáni státuszhiba operátori figyelmeztetéssé válik", async ({ page }) => {
  let statusRequests = 0;
  await mockMissionReadModels(page);
  await page.route("**/api/v1/missions/point", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(checkedMissionPlan) });
  });
  await page.route("**/api/v1/plans/approve", async (route) => {
    await expect(route.request().postDataJSON()).toEqual({ plan_id: checkedMissionPlan.plan_id });
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ text: "E2E: jóváhagyás rögzítve", plan_id: checkedMissionPlan.plan_id }) });
  });
  await page.route(`**/api/v1/plans/${checkedMissionPlan.plan_id}/status`, async (route) => {
    statusRequests += 1;
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "E2E státuszforrás nem elérhető" }) });
  });

  await page.getByRole("tab", { name: "Küldetés" }).click();
  await page.getByRole("button", { name: "Terv ellenőrzése" }).click();
  await page.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" }).click();

  await expect(page.getByRole("status")).toContainText("A végrehajtási állapot nem ellenőrizhető");
  await expect(page.getByRole("region", { name: "Küldetési események" })).toContainText("A végrehajtási állapot figyelése megszakadt.");
  expect(statusRequests).toBe(1);
});

test("a Világ nézet visszatartja a nem igazolt foglaltsági cellákat", async ({ page }) => {
  const prohibitedRequests: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/(plans|missions)\//.test(new URL(request.url()).pathname)) prohibitedRequests.push(request.url());
  });
  await page.route("**/api/v1/world-memory", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ claims: [], disputed: [] }) });
  });
  await page.route("**/api/v1/world-map", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({
      occupancy_only: false,
      cells: [{ north_m: 8, east_m: -3, cell_size_m: 2 }],
    }) });
  });

  await page.getByRole("tab", { name: "Világ" }).click();

  await expect(page.getByRole("status").filter({ hasText: "foglaltsági jelentése nem igazolt" })).toBeVisible();
  await expect(page.getByLabel("Mért akadály: É 8 m, K -3 m")).toHaveCount(0);
  await expect(page.getByText("Az üres terület ismeretlen, nem szabad vagy biztonságos.")).toBeVisible();
  expect(prohibitedRequests).toEqual([]);
});

test("a jóváhagyásra váró terv visszavonása nem indít végrehajtást", async ({ page }) => {
  let approveRequests = 0;
  let statusRequests = 0;
  await mockMissionReadModels(page);
  await page.route("**/api/v1/missions/point", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(checkedMissionPlan) });
  });
  await page.route("**/api/v1/plans/cancel", async (route) => {
    await expect(route.request().postDataJSON()).toEqual({ plan_id: checkedMissionPlan.plan_id });
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ text: "E2E: terv visszavonva", plan_id: null }) });
  });
  await page.route("**/api/v1/plans/approve", async (route) => {
    approveRequests += 1;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "nem indulhat" }) });
  });
  await page.route(`**/api/v1/plans/${checkedMissionPlan.plan_id}/status`, async (route) => {
    statusRequests += 1;
    await route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "nem kérhető" }) });
  });

  await page.getByRole("tab", { name: "Küldetés" }).click();
  await page.getByRole("button", { name: "Terv ellenőrzése" }).click();
  await expect(page.getByRole("button", { name: "Terv visszavonása" })).toBeVisible();
  await page.getByRole("button", { name: "Terv visszavonása" }).click();

  await expect(page.getByRole("status")).toContainText("E2E: terv visszavonva");
  await expect(page.getByRole("region", { name: "Küldetési események" })).toContainText("A jóváhagyásra váró terv visszavonva.");
  await expect(page.getByRole("button", { name: "Kifejezett jóváhagyás és indítás" })).toHaveCount(0);
  expect(approveRequests).toBe(0);
  expect(statusRequests).toBe(0);
});

test("a validált visszajátszás csak olvasható bizonyítékláncként jelenik meg", async ({ page }) => {
  const replay = {
    id: "audit-run-e2e-001",
    recorded_at: "2026-07-26T08:30:00Z",
    outcome: "completed",
    safety_decision: "approved",
    terminal_phase: "completed",
    failure_reason: null,
    events: [{ phase: "preflight", timestamp: "2026-07-26T08:29:00Z" }, { phase: "completed", timestamp: "2026-07-26T08:30:00Z" }],
    preflight: { battery_percent: 82, navigation_ready: true, home_position_valid: true, global_position_valid: true },
    telemetry: [],
  };
  const controlRequests: string[] = [];
  page.on("request", (request) => {
    if (/\/api\/v1\/(plans|missions)\/(?!replays)/.test(new URL(request.url()).pathname)) controlRequests.push(request.url());
  });
  await page.route("**/api/v1/missions/replays", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ replays: [replay] }) });
  });
  await page.route(`**/api/v1/missions/replays/${replay.id}`, async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(replay) });
  });

  await page.getByRole("tab", { name: "Visszajátszás" }).click();

  await expect(page.getByRole("heading", { name: "Küldetés-visszajátszás" })).toBeVisible();
  await expect(page.getByRole("heading", { name: `Futás: ${replay.id}` })).toBeVisible();
  await expect(page.getByText("SafetyGate jóváhagyta").last()).toBeVisible();
  await expect(page.getByText("Csak olvasható küldetéstörténet. A rögzített futások változatlanok; ez a nézet nem küld parancsot a robotnak.")).toBeVisible();
  await expect(page.getByRole("button", { name: /Visszajátszás megnyitása/ })).toHaveCount(1);
  await expect(page.getByRole("button", { name: /jóváhagyás|indítás|visszavonás/i })).toHaveCount(0);
  expect(controlRequests).toEqual([]);
});
