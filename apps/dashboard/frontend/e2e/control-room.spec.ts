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
