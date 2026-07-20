import assert from "node:assert/strict";
import test from "node:test";

import { diagnosticFailureMessage, runPostTurnMemoryHook, safeMemoryUpdate } from "./post_turn.mjs";

function hookHarness(overrides = {}) {
  const written = [];
  return {
    written,
    options: {
      extract: async () => ({
        kind: "memory_delta",
        operations: [{ op: "upsert", category: "preference", value: "A Baylands világ legyen az alapértelmezett." }],
      }),
      loadFacts: async () => [],
      saveFacts: async (sessionId, facts) => { written.push({ sessionId, facts }); },
      now: () => "2026-07-20T10:00:00Z",
      sessionId: "11111111-2222-3333-4444-555555555555",
      turnId: "turn-1",
      userMessage: "A Baylands világot szeretném alapértelmezettnek.",
      assistantReply: "Rendben, megjegyeztem.",
      ...overrides,
    },
  };
}

test("admitted facts are merged and persisted exactly once", async () => {
  const harness = hookHarness();

  const status = await runPostTurnMemoryHook(harness.options);

  assert.equal(status, "updated");
  assert.equal(harness.written.length, 1);
  assert.deepEqual(harness.written[0].facts.map(({ category, fact }) => ({ category, fact })), [
    { category: "preference", fact: "A Baylands világ legyen az alapértelmezett." },
  ]);
});

test("a failing extractor writes nothing and reports an unavailable hook", async () => {
  const harness = hookHarness({
    extract: async () => { throw new Error("NIM 503 for https://integrate.api.nvidia.com/v1"); },
  });

  const status = await runPostTurnMemoryHook(harness.options);

  assert.equal(status, "unavailable");
  assert.deepEqual(harness.written, []);
});

test("a failing memory write cannot raise into the conversational turn", async () => {
  const harness = hookHarness({
    saveFacts: async () => { throw new Error("EACCES: var/pi-agent/memory"); },
  });

  assert.equal(await runPostTurnMemoryHook(harness.options), "unavailable");
});

test("an empty or rejected proposal leaves memory untouched", async () => {
  const empty = hookHarness({ extract: async () => ({ kind: "memory_delta", operations: [] }) });
  const sensitive = hookHarness({
    extract: async () => ({
      kind: "memory_delta",
      operations: [{ op: "upsert", category: "preference", value: "A jelszavam: titkos123" }],
    }),
  });

  assert.equal(await runPostTurnMemoryHook(empty.options), "skipped");
  assert.equal(await runPostTurnMemoryHook(sensitive.options), "skipped");
  assert.deepEqual([...empty.written, ...sensitive.written], []);
});

test("only allow-listed status words leave the hook boundary", () => {
  assert.equal(safeMemoryUpdate("updated"), "updated");
  assert.equal(safeMemoryUpdate("skipped"), "skipped");
  assert.equal(safeMemoryUpdate("A felhasználó neve Ferenc"), "unavailable");
  assert.equal(safeMemoryUpdate(undefined), "unavailable");
});

test("diagnostics never echo a third-party error message", () => {
  assert.equal(diagnosticFailureMessage(new Error("NVIDIA configuration is missing.")), "NVIDIA configuration is missing.");
  assert.equal(
    diagnosticFailureMessage(new Error("401 Unauthorized for key nvapi-abcdef while sending 'A nevem Ferenc'")),
    "unexpected runner failure",
  );
  assert.equal(diagnosticFailureMessage("A nevem Ferenc"), "unexpected runner failure");
});
