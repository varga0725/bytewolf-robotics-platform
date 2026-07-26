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
